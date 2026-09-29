"""Frozen v4.0 constants and the Phase-P plan: the only source of operations.

Values are transcribed from the protocol/freeze commit
``2ede38f21d4b9d9ba45989eec812213ce9851bee``. Changing any of them is a
protocol amendment (new version, digest and review), never an edit here.

:func:`load_committed_plan` reads the committed plan from its fixed
repository path and accepts it only if its canonical self-digest equals
:data:`PLAN_DIGEST`. :func:`validate_plan` is the single structural validator
for the real plan and for authored synthetic fixture plans; synthetic plans
must name the synthetic source and can never name the real repository or
revision. Both return deeply immutable objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from xlm.data.evidence_v2 import canonical

PROTOCOL_VERSION = "essential-web-evidence-v4.0"
PROTOCOL_SHA256 = "4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727"
FREEZE_DIGEST = "747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57"
PLAN_DIGEST = "16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3"
SCIENTIFIC_ADOPTION_DIGEST = "158c3fc9fed030d0290aa50eded70f7d94b162ab6186ae38aca2ce6b1ebdffd3"
SCIENTIFIC_IDENTITY_DIGEST = "080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6"
FREEZE_COMMIT = "2ede38f21d4b9d9ba45989eec812213ce9851bee"

SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
SOURCE_REPOSITORY = "EssentialAI/essential-web-v1.0"
SOURCE_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
PROJECTION = ("eai_taxonomy", "quality_signals")
M_WINDOW_ROWS = 512

EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4\\essential-web"
SYNTHETIC_REPOSITORY = "synthetic/evidence-v4-fixture"

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0"
PLAN_PATH = f"{EVIDENCE_DIR}/phase_p_plan.json"
FREEZE_PATH = f"{EVIDENCE_DIR}/freeze.json"
ADOPTION_PATH = f"{EVIDENCE_DIR}/scientific_adoption.json"
PROTOCOL_PATH = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md"

# Operational (non-scientific) limits, protocol section 5.
ATTEMPTS_PER_ARM_MAX = 200
BODY_BYTES_PER_ARM_MAX = 67108864
BODY_BYTES_PER_RESPONSE_MAX = 4194304
OVERFLOW_DETECTION_BYTES = 1
REDIRECT_TRANSITIONS_MAX = 3
ADDITIONAL_TRIES_MAX = 2
RETRY_DELAYS_SECONDS: tuple[int, ...] = (1, 2)
RETRYABLE_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)
ATTEMPT_TIMEOUT_SECONDS = 120
ROOT_BYTES_MAX = 268435456
FREE_DISK_BYTES_MIN = 1073741824
FLUSH_INTERVAL_BYTES = 1048576
THRIFT_LIMIT_BYTES = 33554432

CANONICAL_HOST = "huggingface.co"
SIGNED_TARGET_HOST = "cas-bridge.xethub.hf.co"
ALLOWED_HOSTS: tuple[str, ...] = (CANONICAL_HOST, SIGNED_TARGET_HOST)
ALLOWED_PORT = 443
REDIRECT_STATUSES: tuple[int, ...] = (301, 302, 303, 307, 308)

LIMITS: dict[str, Any] = {
    "physical_attempts_per_arm_max": ATTEMPTS_PER_ARM_MAX,
    "response_body_bytes_per_arm_max": BODY_BYTES_PER_ARM_MAX,
    "response_body_bytes_per_response_max": BODY_BYTES_PER_RESPONSE_MAX,
    "overflow_detection_bytes": OVERFLOW_DETECTION_BYTES,
    "redirect_transitions_per_logical_request_max": REDIRECT_TRANSITIONS_MAX,
    "additional_attempts_per_operation_max": ADDITIONAL_TRIES_MAX,
    "retry_delays_seconds": list(RETRY_DELAYS_SECONDS),
    "retryable_http_statuses": list(RETRYABLE_STATUSES),
    "physical_attempt_timeout_seconds": ATTEMPT_TIMEOUT_SECONDS,
    "execution_root_bytes_max": ROOT_BYTES_MAX,
    "free_disk_bytes_min": FREE_DISK_BYTES_MIN,
    "byte_count_flush_interval_bytes": FLUSH_INTERVAL_BYTES,
    "parquet_thrift_limit_bytes": THRIFT_LIMIT_BYTES,
}
NETWORK: dict[str, Any] = {
    "method": "GET",
    "scheme": "https",
    "port": ALLOWED_PORT,
    "hosts": list(ALLOWED_HOSTS),
    "canonical_host": CANONICAL_HOST,
    "signed_target_host": SIGNED_TARGET_HOST,
    "redirect_statuses": list(REDIRECT_STATUSES),
    "request_headers": {"Accept-Encoding": "identity", "Range": "bytes=<start>-<end>"},
    "credentials_sent": False,
}

HEAD = "HEAD_0_3"
M_FOOTER = "M_FOOTER_AND_TRAILER"
T_TRAILER = "T_TRAILER"
T_FOOTER = "T_FOOTER_FROM_TRAILER"
T_FOOTER_RULE = "N-8-L..N-9"
ARM_KINDS: dict[str, tuple[tuple[str, str], ...]] = {
    "M": ((HEAD, "head"), (M_FOOTER, "footer")),
    "T": ((HEAD, "head"), (T_TRAILER, "trailer"), (T_FOOTER, "footer")),
}

_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_STRONG_ETAG = re.compile(r'\A"[\x21\x23-\x7e]+"\Z')
_REPO_SEGMENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._\-]*\Z")
_FILE_SEGMENT = re.compile(r"\A[A-Za-z0-9=_.\-]+\Z")
OP_ID = re.compile(r"\A[MT]-[0-9]{2}-(head|footer|trailer)\Z")


class PlanError(ValueError):
    """The plan is not the frozen plan or violates the plan grammar: refuse."""


@dataclass(frozen=True)
class Source:
    host: str
    repository_type: str
    repository: str
    revision: str

    def canonical_path(self, file: str) -> str:
        return (
            f"/{self.repository_type}/{self.repository}/resolve/"
            f"{quote(self.revision, safe='')}/{quote(file, safe='/=')}"
        )

    def canonical_url(self, file: str) -> str:
        return f"https://{self.host}{self.canonical_path(file)}"


REAL_SOURCE = Source(CANONICAL_HOST, "datasets", SOURCE_REPOSITORY, SOURCE_REVISION)


@dataclass(frozen=True)
class MBindings:
    window: tuple[int, int]
    projection: tuple[str, ...]
    data_chunk_count: int
    data_payload_bytes: int
    data_uncompressed_bytes: int


@dataclass(frozen=True)
class TBindings:
    text_column: str
    span_start: int
    span_end: int  # half-open
    data_range_count: int
    data_payload_bytes: int


@dataclass(frozen=True)
class SourceFile:
    arm: str
    ordinal: int
    file: str
    remote_length: int
    strong_etag: str
    m: MBindings | None
    t: TBindings | None


@dataclass(frozen=True)
class Operation:
    op_id: str
    seq: int
    arm: str
    ordinal: int
    kind: str
    range: tuple[int, int] | None  # inclusive; None only for the T footer
    expected_footer_length: int | None
    source_file: SourceFile


@dataclass(frozen=True)
class Plan:
    digest: str
    synthetic: bool
    source: Source
    execution_root: str
    files: tuple[SourceFile, ...]
    operations: tuple[Operation, ...]

    def operation(self, op_id: str) -> Operation:
        for op in self.operations:
            if op.op_id == op_id:
                return op
        raise PlanError(f"{op_id!r} is not an operation of the frozen plan")

    def sibling(self, op: Operation, kind: str) -> Operation:
        for other in self.operations:
            if other.arm == op.arm and other.ordinal == op.ordinal and other.kind == kind:
                return other
        raise PlanError(f"{op.op_id}: no {kind} operation for this file")


def _keys(obj: Any, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise PlanError(f"{what} must have exactly {sorted(keys)}, got {got}")
    return obj


def _int(value: Any, what: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PlanError(f"{what} must be an integer >= {minimum}")
    return value


def check_file_path(file: Any) -> str:
    if type(file) is not str or not file or file.startswith("/") or len(file) > 512:
        raise PlanError(f"source file path must be a bounded relative path: {file!r}")
    for segment in file.split("/"):
        if segment in (".", "..") or not _FILE_SEGMENT.match(segment):
            raise PlanError(f"source file path has an illegal segment: {file!r}")
    return file


def is_strong_etag(value: Any) -> bool:
    return isinstance(value, str) and bool(_STRONG_ETAG.match(value))


def _source(raw: Any, synthetic: bool) -> Source:
    body = _keys(raw, {"host", "repository_type", "repository", "revision"}, "source")
    source = Source(**{k: body[k] for k in sorted(body)})
    parts = str(source.repository).split("/")
    if source.host != CANONICAL_HOST or source.repository_type != "datasets":
        raise PlanError("source must be a huggingface.co dataset")
    if len(parts) != 2 or not all(_REPO_SEGMENT.match(p) for p in parts):
        raise PlanError("source repository must be owner/name")
    if not _HEX40.match(str(source.revision)):
        raise PlanError("source revision must be an immutable 40-hex commit")
    if not synthetic and source != REAL_SOURCE:
        raise PlanError("real plan source differs from the frozen source identity")
    if synthetic and (
        source.repository != SYNTHETIC_REPOSITORY or source.revision == SOURCE_REVISION
    ):
        raise PlanError("synthetic plans must name the synthetic source, never the real one")
    return source


def _file(raw: Any, arm: str, ordinal: int, synthetic: bool) -> SourceFile:
    body = _keys(
        raw, {"arm", "ordinal", "file", "remote_length", "strong_etag", "bindings"}, "plan file"
    )
    if body["arm"] != arm or body["ordinal"] != ordinal:
        raise PlanError("plan files must be ordered M then T, ordinals 0..n-1")
    file = check_file_path(body["file"])
    length = _int(body["remote_length"], "remote_length", minimum=16)
    if not is_strong_etag(body["strong_etag"]):
        raise PlanError(f"{file}: expected ETag must be a strong ETag")
    m: MBindings | None = None
    t: TBindings | None = None
    if arm == "M":
        b = _keys(
            body["bindings"],
            {
                "window",
                "projection",
                "data_chunk_count",
                "data_payload_bytes",
                "data_uncompressed_bytes",
            },
            "M bindings",
        )
        window = b["window"]
        if not isinstance(window, list) or len(window) != 2:
            raise PlanError(f"{file}: invalid window")
        start, end = _int(window[0], "window start"), _int(window[1], "window end")
        if end <= start or (not synthetic and end - start != M_WINDOW_ROWS):
            raise PlanError(f"{file}: window must hold {M_WINDOW_ROWS} rows")
        if b["projection"] != list(PROJECTION):
            raise PlanError(f"{file}: projection differs from the frozen projection")
        m = MBindings(
            window=(start, end),
            projection=PROJECTION,
            data_chunk_count=_int(b["data_chunk_count"], "data_chunk_count", 1),
            data_payload_bytes=_int(b["data_payload_bytes"], "data_payload_bytes", 1),
            data_uncompressed_bytes=_int(
                b["data_uncompressed_bytes"], "data_uncompressed_bytes", 1
            ),
        )
    else:
        b = _keys(
            body["bindings"],
            {"text_column", "data_span_half_open", "data_range_count", "data_payload_bytes"},
            "T bindings",
        )
        span = b["data_span_half_open"]
        if not isinstance(span, list) or len(span) != 2:
            raise PlanError(f"{file}: invalid text span")
        s0, s1 = _int(span[0], "span start", 4), _int(span[1], "span end", 5)
        if not s0 < s1 <= length - 8:
            raise PlanError(f"{file}: text span must lie inside the file body")
        if b["text_column"] != "text":
            raise PlanError(f"{file}: the T text column binding must be 'text'")
        payload = _int(b["data_payload_bytes"], "data_payload_bytes", 1)
        if payload != s1 - s0:
            raise PlanError(f"{file}: text payload must equal the span length")
        t = TBindings("text", s0, s1, _int(b["data_range_count"], "data_range_count", 1), payload)
    return SourceFile(arm, ordinal, file, length, body["strong_etag"], m, t)


def _operation(raw: Any, seq: int, source_file: SourceFile, kind: str, suffix: str) -> Operation:
    n = source_file.remote_length
    op_id = f"{source_file.arm}-{source_file.ordinal:02d}-{suffix}"
    common = {"op_id", "seq", "arm", "ordinal", "kind", "range"}
    footer_length: int | None = None
    span: tuple[int, int] | None
    if kind == M_FOOTER:
        body = _keys(raw, common | {"expected_footer_length"}, "operation")
    elif kind == T_FOOTER:
        body = _keys(raw, common | {"range_rule"}, "operation")
    else:
        body = _keys(raw, common, "operation")
    if (body["op_id"], body["seq"], body["arm"], body["ordinal"], body["kind"]) != (
        op_id,
        seq,
        source_file.arm,
        source_file.ordinal,
        kind,
    ):
        raise PlanError(f"operation {seq} must be {op_id} ({kind})")
    rng = body["range"]
    if kind == HEAD:
        if rng != [0, 3]:
            raise PlanError(f"{op_id}: identity range must be exactly [0, 3]")
        span = (0, 3)
    elif kind == M_FOOTER:
        if not isinstance(rng, list) or len(rng) != 2:
            raise PlanError(f"{op_id}: footer range malformed")
        start = _int(rng[0], "footer start", 4)
        footer_length = _int(body["expected_footer_length"], "expected_footer_length", 1)
        if rng[1] != n - 1 or footer_length != n - 8 - start:
            raise PlanError(f"{op_id}: footer range must be exactly [N-8-L, N-1]")
        if n - start > BODY_BYTES_PER_RESPONSE_MAX:
            raise PlanError(f"{op_id}: footer range exceeds the 4 MiB response cap")
        span = (start, n - 1)
    elif kind == T_TRAILER:
        if rng != [n - 8, n - 1]:
            raise PlanError(f"{op_id}: trailer range must be exactly [N-8, N-1]")
        span = (n - 8, n - 1)
    else:
        if rng is not None or body["range_rule"] != T_FOOTER_RULE:
            raise PlanError(f"{op_id}: the T footer range is derived from L, never planned")
        span = None
    return Operation(
        op_id, seq, source_file.arm, source_file.ordinal, kind, span, footer_length, source_file
    )


PLAN_KEYS = {
    "kind",
    "protocol_version",
    "plan_schema_version",
    "synthetic",
    "phase",
    "scientific_namespace",
    "selection_digest",
    "policy_digest",
    "source",
    "execution_root",
    "arms",
    "files",
    "operations",
    "limits",
    "network",
    "digest",
}


def validate_plan(obj: Any, *, synthetic: bool) -> Plan:
    """Structurally validate a plan object; the real plan must be THE frozen plan."""
    body = _keys(obj, PLAN_KEYS, "plan")
    if canonical.self_digest(body) != body["digest"]:
        raise PlanError("plan self-digest mismatch")
    if body["kind"] != "essential_web_v4_phase_p_plan" or body["phase"] != "P":
        raise PlanError("not an essential-web v4 Phase-P plan")
    if body["protocol_version"] != PROTOCOL_VERSION or body["plan_schema_version"] != 1:
        raise PlanError("plan protocol/schema version mismatch")
    if body["synthetic"] is not synthetic:
        raise PlanError("synthetic and real plans are not interchangeable")
    if (body["scientific_namespace"], body["selection_digest"], body["policy_digest"]) != (
        SCIENTIFIC_NAMESPACE,
        SELECTION_DIGEST,
        POLICY_DIGEST,
    ):
        raise PlanError("plan scientific bindings differ from the adopted identity")
    if body["limits"] != LIMITS or body["network"] != NETWORK:
        raise PlanError("plan limits/network policy differ from the frozen values")
    if body["arms"] != ["M", "T"]:
        raise PlanError("plan arms must be exactly [M, T]")
    source = _source(body["source"], synthetic)
    if not synthetic and (
        body["digest"] != PLAN_DIGEST or body["execution_root"] != EXECUTION_ROOT
    ):
        raise PlanError("real plan digest/root differ from the frozen plan")
    files_raw = body["files"]
    ops_raw = body["operations"]
    if not isinstance(files_raw, list) or not isinstance(ops_raw, list):
        raise PlanError("plan files/operations must be lists")
    files: list[SourceFile] = []
    for arm in ("M", "T"):
        arm_raw = [f for f in files_raw if isinstance(f, dict) and f.get("arm") == arm]
        if not 1 <= len(arm_raw) <= 8 or (not synthetic and len(arm_raw) != 8):
            raise PlanError(f"plan must hold exactly 8 {arm} files")
        files += [_file(raw, arm, i, synthetic) for i, raw in enumerate(arm_raw)]
    if len(files) != len(files_raw) or [f.arm for f in files] != [
        f.get("arm") for f in files_raw if isinstance(f, dict)
    ]:
        raise PlanError("plan files must be exactly the M files followed by the T files")
    if len({(f.arm, f.file) for f in files}) != len(files):
        raise PlanError("plan files must be unique within an arm")
    operations: list[Operation] = []
    for source_file in files:
        for kind, suffix in ARM_KINDS[source_file.arm]:
            seq = len(operations)
            if seq >= len(ops_raw):
                raise PlanError("plan is missing operations")
            operations.append(_operation(ops_raw[seq], seq, source_file, kind, suffix))
    if len(operations) != len(ops_raw):
        raise PlanError("plan holds operations beyond the fixed per-file structure")
    return Plan(
        digest=body["digest"],
        synthetic=synthetic,
        source=source,
        execution_root=str(body["execution_root"]),
        files=tuple(files),
        operations=tuple(operations),
    )


def load_committed_plan() -> Plan:
    """Load THE frozen plan from its fixed repository path (no caller input)."""
    raw = (REPO_ROOT / PLAN_PATH).read_bytes()
    try:
        obj = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise PlanError(f"committed plan is not strict JSON: {exc}") from exc
    if canonical.canonical_bytes(obj) != raw:
        raise PlanError("committed plan bytes are not canonical")
    if not isinstance(obj, dict) or obj.get("digest") != PLAN_DIGEST:
        raise PlanError("committed plan digest differs from the frozen plan digest")
    return validate_plan(obj, synthetic=False)


def derive_t_footer_range(op: Operation, trailer: bytes) -> tuple[int, int, int]:
    """Protocol section 6: after a valid trailer, GET N-8-L..N-9. Returns (start, end, L)."""
    if op.kind != T_FOOTER or op.source_file.t is None:
        raise PlanError("only the T footer operation derives its range from L")
    if len(trailer) != 8 or trailer[4:] != b"PAR1":
        raise PlanError(f"{op.op_id}: trailer must be 8 bytes ending in PAR1")
    footer_length = int.from_bytes(trailer[:4], "little")
    n = op.source_file.remote_length
    start = n - 8 - footer_length
    if not 1 <= footer_length <= BODY_BYTES_PER_RESPONSE_MAX:
        raise PlanError(f"{op.op_id}: footer length {footer_length} is outside [1, 4 MiB]")
    if start < 4 or start < op.source_file.t.span_end:
        raise PlanError(
            f"{op.op_id}: derived footer start {start} would reach the file header or the "
            f"frozen text chunk (ends {op.source_file.t.span_end})"
        )
    return start, n - 9, footer_length


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))
