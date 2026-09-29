"""Authorized Phase-P plans: the ONLY source of executable operations.

The executor never accepts a file, URL, range or operation kind from a
caller. It loads a plan artifact, validates it here, and iterates the
fixed operation list. Every operation carries its arm, file, operation
class, exact inclusive range (or, for the T footer, the frozen rule that
derives it from the trailer length L), expected identity and budget
category. Labels come from the plan, never from callers.

REAL plans: the committed child artifact must (1) verify its canonical
self-digest, (2) be listed with matching bytes/SHA-256 in the committed
child manifest, and (3) have a payload canonically equal to the plan
recomputed from ``freeze.json`` by :mod:`schedules`.
SYNTHETIC plans: same grammar and ceiling formula, ``synthetic: true``;
they are refused by REAL mode.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import frozen_v3, netpolicy, schedules, trust


class PlanError(ValueError):
    """Plan schema, digest or frozen-binding violation: refuse."""


CHILD_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD"
FREEZE_PATH = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json"
PLAN_FILES = {"M": "arm_m_phase_p_dry.json", "T": "arm_t_phase_p_dry.json"}
PLAN_KINDS = {"M": "essential_web_v3_m_phase_p_plan", "T": "essential_web_v3_t_phase_p_plan"}
MANIFEST_FILE = "artifact_manifest.json"
MANIFEST_KIND = "essential_web_v3_artifact_manifest"
# Synthetic plans may only name this synthetic source: a synthetic epoch can
# never direct the executor at the real repository/revision.
SYNTHETIC_REPOSITORY = "synthetic/evidence-v3-fixture"

ENVELOPE_KEYS = frozenset(
    {
        "kind",
        "protocol_version",
        "epoch_id",
        "epoch_state",
        "arm",
        "authorization",
        "executable",
        "parents",
        "producer",
        "payload",
        "digest",
    }
)
PAYLOAD_KEYS = frozenset(
    {
        "plan_schema",
        "plan_schema_version",
        "arm",
        "phase",
        "synthetic",
        "source",
        "files",
        "ceilings",
        "arithmetic",
    }
)
M_FILE_KEYS = frozenset(
    {"ordinal", "file", "remote_length", "strong_etag", "operations", "bindings", "d_reserve"}
)
M_BINDING_KEYS = frozenset(
    {"window", "projection", "data_chunk_count", "data_payload_bytes", "data_uncompressed_bytes"}
)
T_BINDING_KEYS = frozenset(
    {"text_column", "data_span_half_open", "data_range_count", "data_payload_bytes"}
)
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def _int(value: Any, what: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PlanError(f"{what} must be an integer >= {minimum}")
    return value


def _exact_keys(obj: Any, keys: frozenset[str], what: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise PlanError(f"{what} must have exactly {sorted(keys)}, got {got}")
    return obj


@dataclass(frozen=True)
class PlanOperation:
    """One authorized logical operation (immutable, plan-derived)."""

    arm: str
    index: int  # position in the arm's ordered operation list
    file_ordinal: int
    file: str
    op: str
    range: tuple[int, int] | None  # inclusive; None only for T_FOOTER_FROM_TRAILER
    remote_length: int
    strong_etag: str
    expected_footer_length: int | None
    category: str = "footer"

    @property
    def magic(self) -> str:
        if self.op == "IDENTITY_HEAD_0_3":
            return "head"
        if self.op in ("M_FOOTER_AND_TRAILER", "T_TRAILER"):
            return "tail"
        return "none"


@dataclass(frozen=True)
class FilePlan:
    ordinal: int
    file: str
    remote_length: int
    strong_etag: str
    bindings: Mapping[str, Any]
    ceiling: Mapping[str, int]
    d_reserve: Mapping[str, int]


class ValidatedPhasePPlan(trust.TrustedObject):
    """A validated, immutable per-arm Phase-P plan."""

    __slots__ = (
        "arm",
        "digest",
        "synthetic",
        "source",
        "files",
        "operations",
        "arm_ceiling",
        "response_body_bytes_max",
        "time_seconds",
        "payload_digest",
    )
    arm: str
    digest: str
    synthetic: bool
    source: netpolicy.SourceIdentity
    files: tuple[FilePlan, ...]
    operations: tuple[PlanOperation, ...]
    arm_ceiling: Mapping[str, int]
    response_body_bytes_max: int
    time_seconds: Mapping[str, int]
    payload_digest: str

    def file_plan(self, file: str) -> FilePlan:
        for entry in self.files:
            if entry.file == file:
                return entry
        raise PlanError(f"{file!r} is not in the authorized {self.arm} plan")


def derive_t_footer_range(op: PlanOperation, trailer: bytes) -> tuple[int, int]:
    """Frozen rule: after a valid trailer establishes L, GET N-8-L..N-9."""
    if op.op != "T_FOOTER_FROM_TRAILER":
        raise PlanError("only the T footer operation derives its range from L")
    if len(trailer) != 8 or trailer[4:] != b"PAR1":
        raise PlanError("trailer must be 8 bytes ending in PAR1")
    footer_length = int.from_bytes(trailer[:4], "little")
    n = op.remote_length
    if footer_length <= 0 or n - 8 - footer_length < 4:
        raise PlanError(f"trailer footer length {footer_length} is impossible for length {n}")
    return n - 8 - footer_length, n - 9


def _check_payload(payload: Any, *, arm: str, synthetic: bool) -> dict[str, Any]:
    body = dict(_exact_keys(payload, PAYLOAD_KEYS, "plan payload"))
    if body["plan_schema"] != schedules.PLAN_SCHEMA:
        raise PlanError("plan schema mismatch")
    if body["plan_schema_version"] != schedules.PLAN_SCHEMA_VERSION:
        raise PlanError("plan schema version mismatch")
    if body["arm"] != arm or body["phase"] != "P":
        raise PlanError("plan arm/phase mismatch")
    if body["synthetic"] is not synthetic:
        raise PlanError("synthetic plans and real plans are not interchangeable")
    return body


def _build(envelope: Mapping[str, Any], *, arm: str, synthetic: bool) -> ValidatedPhasePPlan:
    _exact_keys(envelope, ENVELOPE_KEYS, "plan artifact")
    if envelope["kind"] != PLAN_KINDS[arm] or envelope["arm"] != arm:
        raise PlanError("plan artifact kind/arm mismatch")
    if envelope["authorization"] != "NONE" or envelope["executable"] is not False:
        raise PlanError("child plan artifacts carry authorization NONE / executable false")
    if envelope["epoch_id"] != frozen_v3.EPOCH_ID:
        raise PlanError("plan binds a different epoch")
    if envelope["protocol_version"] != frozen_v3.PROTOCOL_VERSION:
        raise PlanError("plan binds a different protocol version")
    if canonical.self_digest(envelope) != envelope["digest"]:
        raise PlanError("plan artifact self-digest mismatch")
    body = _check_payload(envelope["payload"], arm=arm, synthetic=synthetic)
    source_raw = _exact_keys(
        body["source"], frozenset({"host", "repository_type", "repository", "revision"}), "source"
    )
    source = netpolicy.SourceIdentity(
        host=str(source_raw["host"]),
        repository_type=str(source_raw["repository_type"]),
        repository=str(source_raw["repository"]),
        revision=str(source_raw["revision"]),
    )
    try:
        netpolicy.check_source_identity(source)
    except netpolicy.PolicyError as exc:
        raise PlanError(str(exc)) from exc
    if not synthetic and source != netpolicy.FROZEN_SOURCE:
        raise PlanError("real plan source differs from the frozen source identity")
    if synthetic and (
        source.repository != SYNTHETIC_REPOSITORY or source.revision == frozen_v3.SOURCE_REVISION
    ):
        raise PlanError("synthetic plans must name the synthetic source, never the real one")
    files_raw = body["files"]
    if not isinstance(files_raw, list) or not files_raw or len(files_raw) > 64:
        raise PlanError("plan files must be a bounded non-empty list")
    expected_ops = schedules.M_OPS if arm == "M" else schedules.T_OPS
    reserves: list[dict[str, int]] = []
    files: list[FilePlan] = []
    operations: list[PlanOperation] = []
    seen: set[str] = set()
    for ordinal, entry in enumerate(files_raw):
        cell = _exact_keys(entry, M_FILE_KEYS, f"plan file {ordinal}")
        if cell["ordinal"] != ordinal:
            raise PlanError("plan files must be in ordinal order")
        name = cell["file"]
        if type(name) is not str or name in seen:
            raise PlanError("plan file names must be unique strings")
        seen.add(name)
        try:
            source.canonical_url(name)
        except netpolicy.PolicyError as exc:
            raise PlanError(str(exc)) from exc
        length = _int(cell["remote_length"], "remote_length", minimum=16)
        etag = cell["strong_etag"]
        if not netpolicy.is_strong_etag(etag) or len(etag) < 3:
            raise PlanError(f"{name}: expected ETag must be a non-empty strong ETag")
        ops_raw = cell["operations"]
        if not isinstance(ops_raw, list) or [o.get("op") for o in ops_raw] != list(expected_ops):
            raise PlanError(f"{name}: operations must be exactly {list(expected_ops)}")
        reserve_raw = cell["d_reserve"]
        if arm == "M":
            reserve = dict(
                _exact_keys(reserve_raw, frozenset({"requests", "footer_bytes"}), "d_reserve")
            )
            bindings = dict(_exact_keys(cell["bindings"], M_BINDING_KEYS, "M bindings"))
            window = bindings["window"]
            if (
                not isinstance(window, list)
                or len(window) != 2
                or _int(window[1], "window end") - _int(window[0], "window start") <= 0
            ):
                raise PlanError(f"{name}: invalid window")
            if bindings["projection"] != list(frozen_v3.PROJECTION):
                raise PlanError(f"{name}: projection differs from the frozen projection")
            for key in ("data_chunk_count", "data_payload_bytes", "data_uncompressed_bytes"):
                _int(bindings[key], key, minimum=1)
        else:
            reserve = dict(
                _exact_keys(
                    reserve_raw,
                    frozenset({"requests", "footer_bytes", "transfer_bytes"}),
                    "d_reserve",
                )
            )
            bindings = dict(_exact_keys(cell["bindings"], T_BINDING_KEYS, "T bindings"))
            span = bindings["data_span_half_open"]
            if not isinstance(span, list) or len(span) != 2 or not 0 <= span[0] < span[1] <= length:
                raise PlanError(f"{name}: invalid T data span")
            if bindings["text_column"] != "text":
                raise PlanError(f"{name}: T text column binding must be 'text'")
            _int(bindings["data_range_count"], "data_range_count")
            _int(bindings["data_payload_bytes"], "data_payload_bytes", minimum=1)
        for value in reserve.values():
            _int(value, "d_reserve value")
        if reserve["requests"] < schedules.D_IDENTITY_PHYSICAL_PER_FILE:
            raise PlanError(f"{name}: D identity request reservation below 4")
        if reserve["footer_bytes"] != schedules.D_IDENTITY_BODY_BYTES_PER_FILE:
            raise PlanError(f"{name}: D identity byte reservation differs from policy")
        reserves.append(reserve)
        for op_raw in ops_raw:
            op_name = op_raw["op"]
            if op_name == "M_FOOTER_AND_TRAILER":
                _exact_keys(op_raw, frozenset({"op", "range", "expected_footer_length"}), "op")
            else:
                _exact_keys(op_raw, frozenset({"op", "range"}), "op")
            rng = op_raw["range"]
            footer_length: int | None = None
            if op_name == "IDENTITY_HEAD_0_3":
                if rng != [0, 3]:
                    raise PlanError(f"{name}: identity range must be exactly [0, 3]")
                span_t: tuple[int, int] | None = (0, 3)
            elif op_name == "M_FOOTER_AND_TRAILER":
                if not isinstance(rng, list) or len(rng) != 2:
                    raise PlanError(f"{name}: footer range malformed")
                start = _int(rng[0], "footer start", minimum=4)
                if rng[1] != length - 1:
                    raise PlanError(f"{name}: M footer range must end at N-1")
                footer_length = _int(op_raw["expected_footer_length"], "footer length", minimum=1)
                if footer_length != length - 8 - start:
                    raise PlanError(f"{name}: footer length must equal N-8-start")
                if length - start > frozen_v3.RESPONSE_BODY_BYTES_MAX:
                    raise PlanError(f"{name}: footer range exceeds the 4 MiB response cap")
                span_t = (start, length - 1)
            elif op_name == "T_TRAILER":
                if rng != [length - 8, length - 1]:
                    raise PlanError(f"{name}: T trailer range must be exactly N-8..N-1")
                span_t = (length - 8, length - 1)
            else:
                if rng is not None:
                    raise PlanError(f"{name}: T footer range is derived from L, never planned")
                span_t = None
            operations.append(
                PlanOperation(
                    arm=arm,
                    index=len(operations),
                    file_ordinal=ordinal,
                    file=name,
                    op=op_name,
                    range=span_t,
                    remote_length=length,
                    strong_etag=etag,
                    expected_footer_length=footer_length,
                )
            )
        files.append(
            FilePlan(
                ordinal=ordinal,
                file=name,
                remote_length=length,
                strong_etag=etag,
                bindings=bindings,
                ceiling={},
                d_reserve=reserve,
            )
        )
    try:
        recomputed = schedules.phase_p_ceilings(arm, reserves)
    except schedules.ScheduleError as exc:
        raise PlanError(str(exc)) from exc
    if body["ceilings"] != recomputed:
        raise PlanError("plan ceilings differ from frozen caps minus the future-D reservation")
    files = [
        FilePlan(
            ordinal=f.ordinal,
            file=f.file,
            remote_length=f.remote_length,
            strong_etag=f.strong_etag,
            bindings=f.bindings,
            ceiling=dict(recomputed["per_file"][f.ordinal]),
            d_reserve=f.d_reserve,
        )
        for f in files
    ]
    return trust._mint(
        ValidatedPhasePPlan,
        trust._MINT,
        arm=arm,
        digest=str(envelope["digest"]),
        synthetic=synthetic,
        source=source,
        files=tuple(files),
        operations=tuple(operations),
        arm_ceiling=dict(recomputed["arm"]),
        response_body_bytes_max=int(recomputed["response_body_bytes_max"]),
        time_seconds=dict(recomputed["time_seconds"]),
        payload_digest=canonical.digest(body),
    )


def validate_plan_bytes(raw: bytes, *, arm: str, synthetic: bool) -> ValidatedPhasePPlan:
    """Strictly parse and validate one plan artifact (canonical bytes only)."""
    if arm not in ("M", "T"):
        raise PlanError(f"unknown arm {arm!r}")
    try:
        envelope = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise PlanError(f"plan artifact is not strict JSON: {exc}") from exc
    if canonical.canonical_bytes(envelope) != raw:
        raise PlanError("plan artifact bytes are not canonical")
    return _build(envelope, arm=arm, synthetic=synthetic)


@dataclass(frozen=True)
class CommittedChildren:
    """Real committed child plan set, verified against the manifest + freeze."""

    manifest_digest: str
    implementation_commit: str
    code_hashes: Mapping[str, str]
    plans: Mapping[str, ValidatedPhasePPlan]


def _load_freeze(repo: Path) -> dict[str, Any]:
    raw = (repo / FREEZE_PATH).read_bytes()
    freeze: dict[str, Any] = canonical.loads_bytes_strict(raw)
    if canonical.self_digest(freeze) != freeze.get("digest") or (
        freeze["digest"] != frozen_v3.FREEZE_DIGEST
    ):
        raise PlanError("freeze.json does not verify against the frozen digest")
    return freeze


def recompute_real_payloads(repo: Path) -> dict[str, dict[str, Any]]:
    freeze = _load_freeze(repo)
    return {
        "M": schedules.m_phase_p_plan_payload(freeze["M_prospective_files"]),
        "T": schedules.t_phase_p_plan_payload(freeze["T_prospective_files"]),
    }


def load_committed_children(repo: Path) -> CommittedChildren:
    """Load REAL plans from the committed child tree with every binding checked."""
    child = repo / CHILD_DIR
    manifest_raw = (child / MANIFEST_FILE).read_bytes()
    manifest = canonical.loads_bytes_strict(manifest_raw)
    if canonical.canonical_bytes(manifest) != manifest_raw:
        raise PlanError("child manifest bytes are not canonical")
    if canonical.self_digest(manifest) != manifest.get("digest"):
        raise PlanError("child manifest self-digest mismatch")
    if manifest.get("kind") != MANIFEST_KIND or manifest.get("executable") is not False:
        raise PlanError("child manifest kind/executable mismatch")
    listed = manifest["payload"]["artifacts"]
    producer = manifest["producer"]
    commit = producer.get("implementation_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PlanError("child manifest lacks a 40-hex implementation commit")
    code = producer.get("code_hashes")
    if not isinstance(code, dict) or not all(_HEX64.match(str(v)) for v in code.values()):
        raise PlanError("child manifest code identity must be SHA-256 values")
    recomputed = recompute_real_payloads(repo)
    plans: dict[str, ValidatedPhasePPlan] = {}
    for arm, name in PLAN_FILES.items():
        raw = (child / name).read_bytes()
        entry = listed.get(name)
        if not isinstance(entry, dict):
            raise PlanError(f"{name} is not listed in the child manifest")
        if entry.get("sha256") != hashlib.sha256(raw).hexdigest() or entry.get("bytes") != len(raw):
            raise PlanError(f"{name} bytes differ from the child manifest")
        plan = validate_plan_bytes(raw, arm=arm, synthetic=False)
        if entry.get("canonical_digest") != plan.digest:
            raise PlanError(f"{name} digest differs from the child manifest")
        if plan.payload_digest != canonical.digest(recomputed[arm]):
            raise PlanError(f"{name} payload differs from the plan recomputed from freeze.json")
        envelope = canonical.loads_bytes_strict(raw)
        if envelope["producer"] != producer:
            raise PlanError(f"{name} producer identity differs from the manifest producer")
        plans[arm] = plan
    return CommittedChildren(
        manifest_digest=str(manifest["digest"]),
        implementation_commit=commit,
        code_hashes=dict(code),
        plans=plans,
    )
