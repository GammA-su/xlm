"""Offline verification of the committed v4.0 freeze and its exact scientific adoption.

Recomputes, from committed repository bytes only: the protocol SHA-256, the
freeze/plan/adoption self-digests and their cross-bindings, and an independent
comparison of every plan file, window, length, ETag, range and binding with
the committed v3.0 freeze (the adopted scientific planning lineage). No
network, no corpus text, no execution root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen

V3_FREEZE_PATH = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json"
V3_FREEZE_DIGEST = "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"


class VerifyError(ValueError):
    """A committed binding does not reproduce: the freeze is not intact."""


def _strict(rel: str) -> tuple[bytes, dict[str, Any]]:
    raw = (frozen.REPO_ROOT / rel).read_bytes()
    try:
        obj = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise VerifyError(f"{rel}: {exc}") from exc
    if not isinstance(obj, dict) or canonical.self_digest(obj) != obj.get("digest"):
        raise VerifyError(f"{rel}: self-digest mismatch")
    return raw, obj


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def verify_repository() -> dict[str, Any]:
    protocol = (frozen.REPO_ROOT / frozen.PROTOCOL_PATH).read_bytes()
    protocol_sha = hashlib.sha256(protocol).hexdigest()
    _require(protocol_sha == frozen.PROTOCOL_SHA256, f"protocol bytes drift: {protocol_sha}")
    _, freeze = _strict(frozen.FREEZE_PATH)
    plan_raw, plan_obj = _strict(frozen.PLAN_PATH)
    adoption_raw, adoption = _strict(frozen.ADOPTION_PATH)
    _, v3 = _strict(V3_FREEZE_PATH)
    _require(freeze["digest"] == frozen.FREEZE_DIGEST, "freeze digest drift")
    _require(v3["digest"] == V3_FREEZE_DIGEST, "v3.0 freeze digest drift")
    _require(freeze["protocol"]["sha256"] == protocol_sha, "freeze does not bind the protocol")
    for key, raw, digest in (
        ("phase_p_plan", plan_raw, frozen.PLAN_DIGEST),
        ("scientific_adoption", adoption_raw, frozen.SCIENTIFIC_ADOPTION_DIGEST),
    ):
        binding = freeze[key]
        _require(
            binding["sha256"] == hashlib.sha256(raw).hexdigest()
            and binding["bytes"] == len(raw)
            and binding["digest"] == digest,
            f"freeze binding for {key} does not reproduce",
        )
    _require(freeze["operational_limits"] == frozen.LIMITS, "freeze limits drift")
    _require(freeze["network"] == frozen.NETWORK, "freeze network policy drift")
    _require(freeze["execution_root"] == frozen.EXECUTION_ROOT, "freeze execution root drift")
    plan = frozen.load_committed_plan()
    science = adoption["scientific_identity"]
    _require(science == v3["scientific_identity"], "adopted identity differs from the v3.0 freeze")
    _require(
        canonical.digest(science) == frozen.SCIENTIFIC_IDENTITY_DIGEST
        and adoption["scientific_identity_digest"] == frozen.SCIENTIFIC_IDENTITY_DIGEST,
        "scientific identity digest drift",
    )
    for got, want, what in (
        (science["scientific_namespace"], frozen.SCIENTIFIC_NAMESPACE, "namespace"),
        (science["selection_digest"], frozen.SELECTION_DIGEST, "selection digest"),
        (science["revision"], frozen.SOURCE_REVISION, "revision"),
        (science["policy_digest"], frozen.POLICY_DIGEST, "policy digest"),
        (science["projection"], list(frozen.PROJECTION), "projection"),
        (science["metadata_and_text_seed"], 20260927, "metadata seed"),
        (science["review_order_seed"], 20260928, "review-order seed"),
        (science["selection_total"], 118, "T locator count"),
    ):
        _require(got == want, f"scientific identity drift: {what}")
    m_files = [f for f in plan.files if f.arm == "M"]
    t_files = [f for f in plan.files if f.arm == "T"]
    _require(
        [(f.file, list(f.m.window) if f.m else None) for f in m_files]
        == [(w["file"], w["window"]) for w in science["M_windows"]],
        "M files/windows differ from the adopted scientific identity",
    )
    for f, row in zip(m_files, v3["M_prospective_files"], strict=True):
        assert f.m is not None
        footer = plan.operation(f"M-{f.ordinal:02d}-footer")
        _require(
            (f.file, f.remote_length, f.strong_etag, list(f.m.window))
            == (row["file"], row["remote_length"], row["etag"], row["window"])
            and (f.m.data_chunk_count, f.m.data_payload_bytes, f.m.data_uncompressed_bytes)
            == (row["data_chunk_count"], row["data_payload_bytes"], row["data_uncompressed_bytes"])
            and [[0, 3], list(footer.range or ())] == row["phase_P_ranges_inclusive"],
            f"M {f.file} differs from the v3.0 freeze",
        )
    for f, row in zip(t_files, v3["T_prospective_files"], strict=True):
        assert f.t is not None
        ranges = row["data_ranges_half_open"]
        _require(
            (f.file, f.remote_length, f.strong_etag)
            == (row["file"], row["remote_length"], row["etag"])
            and (f.t.span_start, f.t.span_end) == (ranges[0]["start"], ranges[-1]["end"])
            and (f.t.data_range_count, f.t.data_payload_bytes)
            == (row["data_range_count"], row["data_payload_bytes"]),
            f"T {f.file} differs from the v3.0 freeze",
        )
    _require(
        [d["file"] for d in adoption["T"]["development_files"]] == [f.file for f in t_files],
        "T development files differ from the adoption record",
    )
    ops = plan.operations
    return {
        "protocol_version": frozen.PROTOCOL_VERSION,
        "protocol_sha256": protocol_sha,
        "freeze_digest": freeze["digest"],
        "plan_digest": plan.digest,
        "scientific_adoption_digest": adoption["digest"],
        "scientific_identity_digest": frozen.SCIENTIFIC_IDENTITY_DIGEST,
        "selection_digest": frozen.SELECTION_DIGEST,
        "source_revision": frozen.SOURCE_REVISION,
        "M_files": len(m_files),
        "T_files": len(t_files),
        "M_logical_operations": sum(1 for o in ops if o.arm == "M"),
        "T_logical_operations": sum(1 for o in ops if o.arm == "T"),
        "T_ranges_derived_after_trailer": sum(1 for o in ops if o.range is None),
        "execution_root": frozen.EXECUTION_ROOT,
        "execution_root_exists": Path(frozen.EXECUTION_ROOT).exists(),
    }
