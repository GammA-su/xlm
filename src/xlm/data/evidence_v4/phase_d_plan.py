"""Frozen v4.1 Phase-D constants and plan: the only source of Phase-D operations.

Values are transcribed from the Phase-D protocol/freeze commit (protocol
``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md``).
Changing any of them is a protocol amendment, never an edit here.

The plan is derived from the reviewed dry plan (digest :data:`DRY_PLAN_DIGEST`)
and binds the COMPLETE v4.1 Phase-P parent. It holds exactly 8 M operations
(one exact projected-column range per M file) and 47 T operations (each
dictionary-inclusive ``text`` chunk split from its start into 4 MiB pieces).
:func:`validate_plan` re-derives every operation from the file bindings and
refuses any difference, so no operation can be injected, dropped, widened or
reordered. The fetch side is expressed as an ordinary :class:`frozen.Plan`
so the reviewed v4 engine executes it unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v2 import frozen as v2_frozen
from xlm.data.evidence_v4 import frozen, v41

PROTOCOL_VERSION = "essential-web-evidence-v4.1-phase-d"
FREEZE_COMMIT = "d58693822ce06baddf1d62a21f69ecf0bb81f2bd"
PROTOCOL_SHA256 = "bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f"
FREEZE_DIGEST = "9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228"
PLAN_DIGEST = "23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7"
PARENT_BINDING_DIGEST = "de27e2a56f83970a91f9f3224b3bad7cefc977018612522679838ebd15b61559"
ADOPTION_DIGEST = "4a2de146e53b4a21f1596f1f3518d0a12f5fec2b5760bfab20fd3eabcdea2cba"

DRY_PLAN_DIGEST = "ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356"
RESULT_REVIEW_COMMIT = "ed8efcf3c85c265828a88579a264f08a2048640f"

EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4.1\\essential-web-phase-d"
PHASE_P_ROOT = v41.EXECUTION_ROOT

EVIDENCE_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D"
PLAN_PATH = f"{EVIDENCE_DIR}/phase_d_plan.json"
FREEZE_PATH = f"{EVIDENCE_DIR}/freeze.json"
PARENT_BINDING_PATH = f"{EVIDENCE_DIR}/phase_p_parent_binding.json"
ADOPTION_PATH = f"{EVIDENCE_DIR}/scientific_adoption.json"
PROTOCOL_PATH = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md"
DRY_PLAN_PATH = (
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW/"
    "phase_d_dry_plan.json"
)

M_KIND = "M_PROJECTED_RANGE"
T_KIND = "T_TEXT_CHUNK_RANGE"
OP_ID = re.compile(r"\A[MT]-[0-9]{2}-d[0-9]{2}\Z")

# Operational (non-scientific) limits, protocol section 6.
ATTEMPTS_PER_ARM_MAX = {"M": 64, "T": 320}
BODY_BYTES_PER_ARM_MAX = {"M": 67108864, "T": 536870912}
ROOT_BYTES_MAX = 1073741824
FREE_DISK_BYTES_MIN = 2147483648
PIECE_BYTES = 4194304
DECODE_BATCH_ROWS = 256
M_RECORD_BYTES_MAX = 1048576
M_OUTPUT_BYTES_MAX = 67108864
T_DOCUMENT_BYTES_MAX = 65536
T_RETAINED_TEXT_BYTES_MAX = 8388608
T_OUTPUT_BYTES_MAX = 67108864

LIMITS: dict[str, Any] = {
    "physical_attempts_per_arm_max": dict(ATTEMPTS_PER_ARM_MAX),
    "response_body_bytes_per_arm_max": dict(BODY_BYTES_PER_ARM_MAX),
    "response_body_bytes_per_response_max": frozen.BODY_BYTES_PER_RESPONSE_MAX,
    "overflow_detection_bytes": frozen.OVERFLOW_DETECTION_BYTES,
    "redirect_transitions_per_logical_request_max": frozen.REDIRECT_TRANSITIONS_MAX,
    "additional_attempts_per_operation_max": frozen.ADDITIONAL_TRIES_MAX,
    "retry_delays_seconds": list(frozen.RETRY_DELAYS_SECONDS),
    "retryable_http_statuses": list(frozen.RETRYABLE_STATUSES),
    "physical_attempt_timeout_seconds": frozen.ATTEMPT_TIMEOUT_SECONDS,
    "execution_root_bytes_max": ROOT_BYTES_MAX,
    "free_disk_bytes_min": FREE_DISK_BYTES_MIN,
    "byte_count_flush_interval_bytes": frozen.FLUSH_INTERVAL_BYTES,
    "parquet_thrift_limit_bytes": frozen.THRIFT_LIMIT_BYTES,
    "t_range_piece_bytes": PIECE_BYTES,
    "decode_batch_rows": DECODE_BATCH_ROWS,
    "m_metadata_record_bytes_max": M_RECORD_BYTES_MAX,
    "m_selected_metadata_bytes_max": M_OUTPUT_BYTES_MAX,
    "t_document_utf8_bytes_max": T_DOCUMENT_BYTES_MAX,
    "t_retained_text_bytes_max": T_RETAINED_TEXT_BYTES_MAX,
    "t_selected_documents_bytes_max": T_OUTPUT_BYTES_MAX,
}

# The frozen v2.0 retention contract the T limits inherit (never redefined here).
if (T_DOCUMENT_BYTES_MAX, T_RETAINED_TEXT_BYTES_MAX, DECODE_BATCH_ROWS) != (
    v2_frozen.ARM_T_LIMITS["document_bytes_max"],
    v2_frozen.ARM_T_LIMITS["retained_text_bytes_max"],
    v2_frozen.WINDOW_BATCH_ROWS,
):
    raise RuntimeError("Phase-D retention limits differ from the frozen v2.0 contract")

# Exact real-plan shape (protocol sections 4-5).
REAL_M_OPERATIONS = 8
REAL_T_OPERATIONS = 47
REAL_M_BYTES = 11692530
REAL_T_BYTES = 179963169
REAL_M_ROWS = 4096
REAL_T_LOCATORS = 118

SYNTHETIC_ROOT = "SYNTHETIC"
PARENT_DOCS = (
    "artifact_manifest.json",
    "m_phase_p_layout.json",
    "phase_p_receipt.json",
    "t_phase_p_layout.json",
)
PARENT_FILES = ("request_receipt.jsonl", "state.sqlite")

PROFILE = frozen.Profile(
    label="v4.1 Phase-D",
    protocol_version=PROTOCOL_VERSION,
    protocol_sha256=PROTOCOL_SHA256,
    freeze_digest=FREEZE_DIGEST,
    plan_digest=PLAN_DIGEST,
    plan_path=PLAN_PATH,
    execution_root=EXECUTION_ROOT,
    network=v41.NETWORK,
    hosts=v41.HOSTS,
)


class PlanError(frozen.PlanError):
    """The Phase-D plan is not the frozen plan or violates its grammar: refuse."""


@dataclass(frozen=True)
class Chunk:
    path: str
    start: int
    end_exclusive: int
    compressed_bytes: int


@dataclass(frozen=True)
class MFile:
    ordinal: int
    file: str
    remote_length: int
    strong_etag: str
    window: tuple[int, int]  # absolute, half-open
    row_group: int
    row_group_first_row: int
    row_group_rows: int
    chunks: tuple[Chunk, ...]
    range_half_open: tuple[int, int]
    uncompressed_bytes: int
    footer_payload: str
    footer_start: int


@dataclass(frozen=True)
class Locator:
    identity: tuple[str, str, str, int]  # [repository, revision, file, absolute row]
    row_in_group: int


@dataclass(frozen=True)
class TFile:
    ordinal: int
    file: str
    remote_length: int
    strong_etag: str
    row_group: int
    row_group_first_row: int
    row_group_rows: int
    span: tuple[int, int]  # dictionary-inclusive text chunk, half-open
    dictionary_page_offset: int
    data_page_offset: int
    uncompressed_bytes: int
    footer_payload: str
    trailer_payload: str
    footer_start: int
    locators: tuple[Locator, ...]


@dataclass(frozen=True)
class ParentArtifact:
    rel: str
    bytes: int
    sha256: str
    digest: str | None  # self-digest for the four JSON exports


@dataclass(frozen=True)
class Parents:
    digest: str  # H(parents object)
    dry_plan_path: str
    dry_plan_bytes: int
    dry_plan_sha256: str
    dry_plan_digest: str
    result_review_commit: str
    phase_p_root: str
    phase_p_plan_digest: str
    artifacts: tuple[ParentArtifact, ...]

    def artifact(self, rel: str) -> ParentArtifact:
        for item in self.artifacts:
            if item.rel == rel:
                return item
        raise PlanError(f"{rel!r} is not a bound Phase-P parent artifact")


@dataclass(frozen=True)
class PhaseDPlan:
    digest: str
    synthetic: bool
    fetch: frozen.Plan  # the fetch operations, executed by the reviewed v4 engine
    m_files: tuple[MFile, ...]
    t_files: tuple[TFile, ...]
    parents: Parents

    def ops_of(self, arm: str, ordinal: int) -> tuple[frozen.Operation, ...]:
        return tuple(o for o in self.fetch.operations if (o.arm, o.ordinal) == (arm, ordinal))


def _keys(obj: Any, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise PlanError(f"{what} must have exactly {sorted(keys)}, got {got}")
    return obj


def _int(value: Any, what: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PlanError(f"{what} must be an integer >= {minimum}")
    return value


def _hex64(value: Any, what: str) -> str:
    if not frozen.is_hex64(value):
        raise PlanError(f"{what} must be a lowercase SHA-256 hex digest")
    return str(value)


def _pair(value: Any, what: str, minimum: int = 0) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise PlanError(f"{what} must be [start, end)")
    start, end = _int(value[0], what, minimum), _int(value[1], what, minimum)
    if end <= start:
        raise PlanError(f"{what} must be non-empty")
    return start, end


def split_pieces(start: int, end: int) -> list[tuple[int, int]]:
    """Half-open [start, end) into 4 MiB pieces from the start plus the remainder."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + PIECE_BYTES, end)
        pieces.append((cursor, stop))
        cursor = stop
    return pieces


def _parents(raw: Any, file_counts: tuple[int, int], synthetic: bool) -> Parents:
    body = _keys(raw, {"dry_plan", "result_review", "phase_p"}, "parents")
    dry = _keys(body["dry_plan"], {"path", "bytes", "sha256", "digest"}, "parents.dry_plan")
    review = _keys(
        body["result_review"],
        {"commit", "report", "review_result", "verdict"},
        "parents.result_review",
    )
    phase_p = _keys(
        body["phase_p"],
        {
            "root",
            "protocol_version",
            "protocol_sha256",
            "freeze_digest",
            "plan_digest",
            "membership_and_ranges_digest",
            "run_status",
            "artifacts",
        },
        "parents.phase_p",
    )
    for key in ("report", "review_result"):
        _keys(review[key], {"path", "bytes", "sha256"}, f"parents.result_review.{key}")
    if not isinstance(review["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", review["commit"]):
        raise PlanError("result-review commit must be a 40-hex commit")
    if phase_p["run_status"] != "COMPLETE" or phase_p["protocol_version"] != v41.PROTOCOL_VERSION:
        raise PlanError("Phase D binds only a COMPLETE v4.1 Phase-P parent")
    for key in ("protocol_sha256", "freeze_digest", "plan_digest", "membership_and_ranges_digest"):
        _hex64(phase_p[key], f"parents.phase_p.{key}")
    _hex64(dry["digest"], "dry plan digest")
    _hex64(dry["sha256"], "dry plan sha256")
    if not synthetic:
        expected = (
            DRY_PLAN_PATH,
            DRY_PLAN_DIGEST,
            RESULT_REVIEW_COMMIT,
            PHASE_P_ROOT,
            v41.PROTOCOL_SHA256,
            v41.FREEZE_DIGEST,
            v41.PLAN_DIGEST,
            v41.MEMBERSHIP_AND_RANGES_DIGEST,
        )
        got = (
            dry["path"],
            dry["digest"],
            review["commit"],
            phase_p["root"],
            phase_p["protocol_sha256"],
            phase_p["freeze_digest"],
            phase_p["plan_digest"],
            phase_p["membership_and_ranges_digest"],
        )
        if got != expected:
            raise PlanError("real plan parents differ from the reviewed dry plan / Phase-P parent")
    elif phase_p["root"] != SYNTHETIC_ROOT or phase_p["plan_digest"] == v41.PLAN_DIGEST:
        raise PlanError("synthetic plans must bind a synthetic Phase-P parent")
    m_count, t_count = file_counts
    names = set(PARENT_DOCS) | set(PARENT_FILES)
    names |= {f"payload/M-{i:02d}-footer.bin" for i in range(m_count)}
    names |= {f"payload/T-{i:02d}-{k}.bin" for i in range(t_count) for k in ("footer", "trailer")}
    artifacts_raw = phase_p["artifacts"]
    if not isinstance(artifacts_raw, dict) or set(artifacts_raw) != names:
        raise PlanError("Phase-P parent artifacts must be exactly the exports, state and footers")
    artifacts: list[ParentArtifact] = []
    for rel in sorted(names):
        doc = rel in PARENT_DOCS
        item = _keys(artifacts_raw[rel], {"bytes", "sha256"} | ({"digest"} if doc else set()), rel)
        artifacts.append(
            ParentArtifact(
                rel,
                _int(item["bytes"], f"{rel} bytes", 1),
                _hex64(item["sha256"], f"{rel} sha256"),
                _hex64(item["digest"], f"{rel} digest") if doc else None,
            )
        )
    return Parents(
        digest=canonical.digest(body),
        dry_plan_path=str(dry["path"]),
        dry_plan_bytes=_int(dry["bytes"], "dry plan bytes", 1),
        dry_plan_sha256=str(dry["sha256"]),
        dry_plan_digest=str(dry["digest"]),
        result_review_commit=str(review["commit"]),
        phase_p_root=str(phase_p["root"]),
        phase_p_plan_digest=str(phase_p["plan_digest"]),
        artifacts=tuple(artifacts),
    )


M_FILE_KEYS = {
    "arm",
    "ordinal",
    "file",
    "remote_length",
    "strong_etag",
    "window",
    "row_group",
    "row_group_first_row",
    "row_group_rows",
    "projection",
    "projected_chunks",
    "range_half_open",
    "uncompressed_bytes",
    "phase_p_payloads",
    "footer_start",
}
T_FILE_KEYS = {
    "arm",
    "ordinal",
    "file",
    "remote_length",
    "strong_etag",
    "text_column",
    "row_group",
    "row_group_first_row",
    "row_group_rows",
    "chunk_span_half_open",
    "dictionary_page_offset",
    "data_page_offset",
    "compressed_bytes",
    "uncompressed_bytes",
    "phase_p_payloads",
    "footer_start",
    "locators",
}


def _common(body: dict[str, Any], arm: str, ordinal: int) -> tuple[str, int, str]:
    if body["arm"] != arm or body["ordinal"] != ordinal:
        raise PlanError("plan files must be ordered M then T, ordinals 0..n-1")
    file = frozen.check_file_path(body["file"])
    length = _int(body["remote_length"], "remote_length", 16)
    if not frozen.is_strong_etag(body["strong_etag"]):
        raise PlanError(f"{file}: expected ETag must be a strong ETag")
    return file, length, str(body["strong_etag"])


def _row_group(body: dict[str, Any], what: str) -> tuple[int, int, int]:
    return (
        _int(body["row_group"], f"{what} row_group"),
        _int(body["row_group_first_row"], f"{what} row_group_first_row"),
        _int(body["row_group_rows"], f"{what} row_group_rows", 1),
    )


def _m_file(raw: Any, ordinal: int, synthetic: bool) -> MFile:
    body = _keys(raw, M_FILE_KEYS, "M file")
    file, length, etag = _common(body, "M", ordinal)
    what = f"M-{ordinal:02d}"
    rg, first, rows = _row_group(body, what)
    window = _pair(body["window"], f"{what} window")
    if not first <= window[0] < window[1] <= first + rows:
        raise PlanError(f"{what}: window must lie inside its row group")
    if not synthetic and window[1] - window[0] != frozen.M_WINDOW_ROWS:
        raise PlanError(f"{what}: window must hold {frozen.M_WINDOW_ROWS} rows")
    if body["projection"] != list(frozen.PROJECTION):
        raise PlanError(f"{what}: projection differs from the frozen projection")
    chunks_raw = body["projected_chunks"]
    if not isinstance(chunks_raw, list) or not chunks_raw:
        raise PlanError(f"{what}: projected chunks must be a non-empty list")
    chunks: list[Chunk] = []
    for item in chunks_raw:
        c = _keys(item, {"path", "start", "end_exclusive", "compressed_bytes"}, "chunk")
        path = c["path"]
        if not isinstance(path, str) or path.split(".")[0] not in frozen.PROJECTION:
            raise PlanError(f"{what}: chunk path {path!r} is outside the projection")
        start, end = _pair([c["start"], c["end_exclusive"]], f"{what} chunk", 4)
        if _int(c["compressed_bytes"], "compressed_bytes", 1) != end - start:
            raise PlanError(f"{what}: chunk size differs from its span")
        if chunks and start != chunks[-1].end_exclusive:
            raise PlanError(f"{what}: projected chunks must be exactly adjacent, ascending")
        chunks.append(Chunk(path, start, end, end - start))
    if len({c.path for c in chunks}) != len(chunks):
        raise PlanError(f"{what}: duplicate projected chunk path")
    span = _pair(body["range_half_open"], f"{what} range", 4)
    if span != (chunks[0].start, chunks[-1].end_exclusive):
        raise PlanError(f"{what}: range must be exactly the union of the projected chunks")
    if span[1] - span[0] > frozen.BODY_BYTES_PER_RESPONSE_MAX:
        raise PlanError(f"{what}: range exceeds the 4 MiB response cap")
    footer_start = _int(body["footer_start"], "footer_start", 5)
    if not span[1] <= footer_start < length - 8:
        raise PlanError(f"{what}: range must end before the Phase-P footer")
    footer = f"payload/M-{ordinal:02d}-footer.bin"
    if body["phase_p_payloads"] != {"footer": footer}:
        raise PlanError(f"{what}: Phase-P footer payload must be {footer}")
    return MFile(
        ordinal,
        file,
        length,
        etag,
        window,
        rg,
        first,
        rows,
        tuple(chunks),
        span,
        _int(body["uncompressed_bytes"], "uncompressed_bytes", 1),
        footer,
        footer_start,
    )


def _t_file(raw: Any, ordinal: int, source: frozen.Source) -> TFile:
    body = _keys(raw, T_FILE_KEYS, "T file")
    file, length, etag = _common(body, "T", ordinal)
    what = f"T-{ordinal:02d}"
    rg, first, rows = _row_group(body, what)
    if body["text_column"] != "text":
        raise PlanError(f"{what}: the text column must be 'text'")
    span = _pair(body["chunk_span_half_open"], f"{what} span", 4)
    dictionary = _int(body["dictionary_page_offset"], "dictionary_page_offset", 4)
    data = _int(body["data_page_offset"], "data_page_offset", 4)
    if not span[0] == dictionary < data < span[1]:
        raise PlanError(f"{what}: the span must be the dictionary-inclusive text chunk")
    if _int(body["compressed_bytes"], "compressed_bytes", 1) != span[1] - span[0]:
        raise PlanError(f"{what}: compressed bytes must equal the span length")
    footer_start = _int(body["footer_start"], "footer_start", 5)
    if not span[1] <= footer_start < length - 8:
        raise PlanError(f"{what}: span must end before the Phase-P footer")
    payloads = {
        "footer": f"payload/T-{ordinal:02d}-footer.bin",
        "trailer": f"payload/T-{ordinal:02d}-trailer.bin",
    }
    if body["phase_p_payloads"] != payloads:
        raise PlanError(f"{what}: Phase-P payloads must be {payloads}")
    locators_raw = body["locators"]
    if not isinstance(locators_raw, list) or not locators_raw:
        raise PlanError(f"{what}: locators must be a non-empty list")
    locators: list[Locator] = []
    for item in locators_raw:
        loc = _keys(item, {"identity", "row_in_group"}, "locator")
        row_in_group = _int(loc["row_in_group"], "row_in_group")
        identity = loc["identity"]
        expected = [source.repository, source.revision, file, first + row_in_group]
        if identity != expected or type(identity[3]) is not int:
            raise PlanError(f"{what}: locator identity must be {expected}")
        if row_in_group >= rows:
            raise PlanError(f"{what}: locator row lies outside its row group")
        if locators and row_in_group <= locators[-1].row_in_group:
            raise PlanError(f"{what}: locators must be unique and ascending")
        locators.append(Locator((identity[0], identity[1], identity[2], identity[3]), row_in_group))
    return TFile(
        ordinal,
        file,
        length,
        etag,
        rg,
        first,
        rows,
        span,
        dictionary,
        data,
        _int(body["uncompressed_bytes"], "uncompressed_bytes", 1),
        payloads["footer"],
        payloads["trailer"],
        footer_start,
        tuple(locators),
    )


def derive_operations(
    m_files: tuple[MFile, ...], t_files: tuple[TFile, ...]
) -> list[dict[str, Any]]:
    """THE Phase-D operations: one exact range per M file, 4 MiB T pieces, in order."""
    arms: list[tuple[str, str, int, list[tuple[int, int]]]] = [
        *(("M", M_KIND, f.ordinal, [f.range_half_open]) for f in m_files),
        *(("T", T_KIND, f.ordinal, split_pieces(*f.span)) for f in t_files),
    ]
    ops: list[dict[str, Any]] = []
    for arm, kind, ordinal, pieces in arms:
        for piece, (start, end) in enumerate(pieces):
            ops.append(
                {
                    "op_id": f"{arm}-{ordinal:02d}-d{piece:02d}",
                    "seq": len(ops),
                    "arm": arm,
                    "ordinal": ordinal,
                    "piece": piece,
                    "kind": kind,
                    "range": [start, end - 1],
                }
            )
    return ops


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
    "parents",
    "arms",
    "files",
    "operations",
    "limits",
    "network",
    "digest",
}


def validate_plan(obj: Any, *, synthetic: bool) -> PhaseDPlan:
    """Structurally validate a Phase-D plan; the real plan must be THE frozen plan."""
    body = _keys(obj, PLAN_KEYS, "plan")
    if canonical.self_digest(body) != body["digest"]:
        raise PlanError("plan self-digest mismatch")
    if (body["kind"], body["phase"], body["plan_schema_version"]) != (
        "essential_web_v41_phase_d_plan",
        "D",
        1,
    ):
        raise PlanError("not an essential-web v4.1 Phase-D plan")
    if body["protocol_version"] != PROTOCOL_VERSION:
        raise PlanError("plan protocol version mismatch")
    if body["synthetic"] is not synthetic:
        raise PlanError("synthetic and real plans are not interchangeable")
    if (body["scientific_namespace"], body["selection_digest"], body["policy_digest"]) != (
        frozen.SCIENTIFIC_NAMESPACE,
        frozen.SELECTION_DIGEST,
        frozen.POLICY_DIGEST,
    ):
        raise PlanError("plan scientific bindings differ from the adopted identity")
    if body["limits"] != LIMITS or body["network"] != v41.NETWORK:
        raise PlanError("plan limits/network policy differ from the frozen values")
    if body["arms"] != ["M", "T"]:
        raise PlanError("plan arms must be exactly [M, T]")
    try:
        source = frozen._source(body["source"], synthetic)
    except frozen.PlanError as exc:
        raise PlanError(str(exc)) from exc
    if not synthetic and (
        body["digest"] != PLAN_DIGEST or body["execution_root"] != EXECUTION_ROOT
    ):
        raise PlanError("real plan digest/root differ from the frozen Phase-D plan")
    if synthetic and body["execution_root"] != SYNTHETIC_ROOT:
        raise PlanError("synthetic plans must name the synthetic root")
    files_raw = body["files"]
    if not isinstance(files_raw, list):
        raise PlanError("plan files must be a list")
    m_raw = [f for f in files_raw if isinstance(f, dict) and f.get("arm") == "M"]
    t_raw = [f for f in files_raw if isinstance(f, dict) and f.get("arm") == "T"]
    if files_raw != [*m_raw, *t_raw]:
        raise PlanError("plan files must be exactly the M files followed by the T files")
    for arm, items in (("M", m_raw), ("T", t_raw)):
        if not 1 <= len(items) <= 8 or (not synthetic and len(items) != 8):
            raise PlanError(f"plan must hold exactly 8 {arm} files")
    m_files = tuple(_m_file(raw, i, synthetic) for i, raw in enumerate(m_raw))
    t_files = tuple(_t_file(raw, i, source) for i, raw in enumerate(t_raw))
    for files in (m_files, t_files):
        if len({f.file for f in files}) != len(files):
            raise PlanError("plan files must be unique within an arm")
    if body["operations"] != derive_operations(m_files, t_files):
        raise PlanError("plan operations differ from the ranges derived from the file bindings")
    parents = _parents(body["parents"], (len(m_files), len(t_files)), synthetic)
    if not synthetic:
        m_ops = [o for o in body["operations"] if o["arm"] == "M"]
        t_ops = [o for o in body["operations"] if o["arm"] == "T"]
        shape = (
            len(m_ops),
            len(t_ops),
            sum(o["range"][1] - o["range"][0] + 1 for o in m_ops),
            sum(o["range"][1] - o["range"][0] + 1 for o in t_ops),
            sum(f.window[1] - f.window[0] for f in m_files),
            sum(len(f.locators) for f in t_files),
        )
        if shape != (
            REAL_M_OPERATIONS,
            REAL_T_OPERATIONS,
            REAL_M_BYTES,
            REAL_T_BYTES,
            REAL_M_ROWS,
            REAL_T_LOCATORS,
        ):
            raise PlanError(f"real plan shape {shape} differs from the reviewed dry plan")
    source_files: dict[tuple[str, int], frozen.SourceFile] = {}
    for arm, identities in (
        ("M", [(f.ordinal, f.file, f.remote_length, f.strong_etag) for f in m_files]),
        ("T", [(g.ordinal, g.file, g.remote_length, g.strong_etag) for g in t_files]),
    ):
        for ordinal, file, length, etag in identities:
            source_files[(arm, ordinal)] = frozen.SourceFile(
                arm, ordinal, file, length, etag, None, None
            )
    operations = tuple(
        frozen.Operation(
            op["op_id"],
            op["seq"],
            op["arm"],
            op["ordinal"],
            op["kind"],
            (op["range"][0], op["range"][1]),
            None,
            source_files[(op["arm"], op["ordinal"])],
        )
        for op in body["operations"]
    )
    fetch = frozen.Plan(
        digest=str(body["digest"]),
        synthetic=synthetic,
        source=source,
        execution_root=str(body["execution_root"]),
        files=tuple(source_files.values()),
        operations=operations,
        profile=PROFILE,
    )
    return PhaseDPlan(str(body["digest"]), synthetic, fetch, m_files, t_files, parents)


def load_committed_plan() -> PhaseDPlan:
    """Load THE frozen Phase-D plan from its fixed repository path (no caller input)."""
    raw = (frozen.REPO_ROOT / PLAN_PATH).read_bytes()
    try:
        obj = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise PlanError(f"committed plan is not strict JSON: {exc}") from exc
    if canonical.canonical_bytes(obj) != raw:
        raise PlanError("committed plan bytes are not canonical")
    if not isinstance(obj, dict) or obj.get("digest") != PLAN_DIGEST:
        raise PlanError("committed plan digest differs from the frozen Phase-D plan digest")
    return validate_plan(obj, synthetic=False)
