"""Authored synthetic fixtures for offline Phase-P integration evidence.

Everything here is synthetic and offline: tiny in-memory Parquet files,
authored plans (``synthetic: true``) built with the same ceiling formula
as the real plans, an HTTPS-like fake server that answers exactly the
requested byte range and issues real 302 responses to a signed-target
host, a controllable clock, and fixture writers for authorization /
approval / review artifacts whose runtime bindings are probed from the
ACTUAL environment (so they pass only where the real validator agrees).

The fake transport performs no network I/O. It is accepted only through a
:class:`~xlm.data.evidence_v3.harness.SyntheticHarness` on a disposable
root; REAL mode never accepts a transport.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    authorization,
    envidentity,
    frozen_v3,
    fsroot,
    netpolicy,
    plan,
    schedules,
    transport,
)
from xlm.data.evidence_v3.harness import EpochPaths, SyntheticHarness

SYNTHETIC_REPOSITORY = plan.SYNTHETIC_REPOSITORY
SYNTHETIC_REVISION = "5" * 40
SYNTHETIC_COMMIT = "a" * 40
SYNTHETIC_SOURCE = netpolicy.SourceIdentity(
    host=frozen_v3.CANONICAL_HOST,
    repository_type="datasets",
    repository=SYNTHETIC_REPOSITORY,
    revision=SYNTHETIC_REVISION,
)
SIGNED_PREFIX = "/xet-bridge-us/"


class SimulatedCrash(BaseException):
    """Process death stand-in: bypasses every ``except Exception`` handler."""


# --------------------------------------------------------------------------
# Parquet fixtures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SyntheticFile:
    name: str
    content: bytes
    arm: str
    bindings: dict[str, Any]
    etag: str

    @property
    def length(self) -> int:
        return len(self.content)

    @property
    def footer_length(self) -> int:
        return int.from_bytes(self.content[-8:-4], "little")


def _parquet(table: Any, row_group_size: int) -> bytes:
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(table, buf, row_group_size=row_group_size, compression="snappy")
    return buf.getvalue()


def make_m_file(index: int, *, rows: int = 3072, group: int = 1024) -> SyntheticFile:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": pa.array([f"m{index}-{i}" for i in range(rows)]),
            "eai_taxonomy": pa.array(
                [
                    {"code": (i * 7 + index) % 97, "label": f"c{(i + index) % 13}"}
                    for i in range(rows)
                ]
            ),
            "quality_signals": pa.array(
                [{"score": float((i * 3 + index) % 101), "flag": i % 2 == 0} for i in range(rows)]
            ),
            "text": pa.array([f"synthetic metadata row {i} of file {index}" for i in range(rows)]),
        }
    )
    content = _parquet(table, group)
    meta = pq.ParquetFile(io.BytesIO(content)).metadata
    window = [group + 76, group + 76 + 512]
    rg = meta.row_group(1)
    chunks = [
        rg.column(j)
        for j in range(rg.num_columns)
        if str(rg.column(j).path_in_schema).split(".")[0] in frozen_v3.PROJECTION
    ]
    bindings = {
        "window": window,
        "projection": list(frozen_v3.PROJECTION),
        "data_chunk_count": len(chunks),
        "data_payload_bytes": sum(int(c.total_compressed_size) for c in chunks),
        "data_uncompressed_bytes": sum(int(c.total_uncompressed_size) for c in chunks),
    }
    name = f"data/crawl=SYN-M-{index:02d}/train-{index:05d}-of-00008.parquet"
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    return SyntheticFile(name=name, content=content, arm="M", bindings=bindings, etag=etag)


def make_t_file(index: int, *, rows: int = 2048, group: int = 512) -> SyntheticFile:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": pa.array([f"t{index}-{i}" for i in range(rows)]),
            "text": pa.array([f"synthetic document {i % 40} for t{index}" for i in range(rows)]),
        }
    )
    content = _parquet(table, group)
    meta = pq.ParquetFile(io.BytesIO(content)).metadata
    rg = meta.row_group(2)
    column = next(
        rg.column(j) for j in range(rg.num_columns) if str(rg.column(j).path_in_schema) == "text"
    )
    start = int(column.data_page_offset)
    if column.dictionary_page_offset is not None and int(column.dictionary_page_offset) > 0:
        start = min(start, int(column.dictionary_page_offset))
    compressed = int(column.total_compressed_size)
    bindings = {
        "text_column": "text",
        "data_span_half_open": [start, start + compressed],
        "data_range_count": 1,
        "data_payload_bytes": compressed,
    }
    name = f"data/crawl=SYN-T-{index:02d}/train-{index:05d}-of-00008.parquet"
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    return SyntheticFile(name=name, content=content, arm="T", bindings=bindings, etag=etag)


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


def plan_bytes(arm: str, files: list[SyntheticFile], *, t_data_ranges: int | None = None) -> bytes:
    """Authored synthetic plan artifact using the real grammar and formula."""
    entries: list[dict[str, Any]] = []
    reserves: list[dict[str, int]] = []
    for ordinal, f in enumerate(files):
        n = f.length
        if arm == "M":
            start = n - 8 - f.footer_length
            reserve = schedules.m_file_d_reserve()
            ops: list[dict[str, Any]] = [
                {"op": "IDENTITY_HEAD_0_3", "range": [0, 3]},
                {
                    "op": "M_FOOTER_AND_TRAILER",
                    "range": [start, n - 1],
                    "expected_footer_length": f.footer_length,
                },
            ]
            bindings = dict(f.bindings)
        else:
            ranges = (
                t_data_ranges if t_data_ranges is not None else int(f.bindings["data_range_count"])
            )
            bindings = dict(f.bindings)
            bindings["data_range_count"] = ranges
            reserve = schedules.t_file_d_reserve(ranges, int(f.bindings["data_payload_bytes"]))
            ops = [
                {"op": "IDENTITY_HEAD_0_3", "range": [0, 3]},
                {"op": "T_TRAILER", "range": [n - 8, n - 1]},
                {"op": "T_FOOTER_FROM_TRAILER", "range": None},
            ]
        reserves.append(reserve)
        entries.append(
            {
                "ordinal": ordinal,
                "file": f.name,
                "remote_length": n,
                "strong_etag": f.etag,
                "operations": ops,
                "bindings": bindings,
                "d_reserve": reserve,
            }
        )
    payload = {
        "plan_schema": schedules.PLAN_SCHEMA,
        "plan_schema_version": schedules.PLAN_SCHEMA_VERSION,
        "arm": arm,
        "phase": "P",
        "synthetic": True,
        "source": {
            "host": SYNTHETIC_SOURCE.host,
            "repository_type": SYNTHETIC_SOURCE.repository_type,
            "repository": SYNTHETIC_SOURCE.repository,
            "revision": SYNTHETIC_SOURCE.revision,
        },
        "files": entries,
        "ceilings": schedules.phase_p_ceilings(arm, reserves),
        "arithmetic": {"synthetic": True},
    }
    envelope: dict[str, Any] = {
        "kind": plan.PLAN_KINDS[arm],
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "epoch_id": frozen_v3.EPOCH_ID,
        "epoch_state": frozen_v3.EPOCH_INITIAL_STATE,
        "arm": arm,
        "authorization": "NONE",
        "executable": False,
        "parents": {},
        "producer": {"synthetic": True},
        "payload": payload,
    }
    envelope["digest"] = canonical.self_digest(envelope)
    return canonical.canonical_bytes(envelope)


# --------------------------------------------------------------------------
# Fake HTTPS server / transport
# --------------------------------------------------------------------------


class FakeClock:
    """Deterministic monotonic clock; sleeps advance it instantly."""

    def __init__(self, start_ns: int = 1_000_000_000_000) -> None:
        self._now = start_ns
        self._lock = threading.Lock()
        self.sleeps: list[float] = []

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += int(seconds * 1_000_000_000)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def utc_now(self) -> str:
        return "2026-09-29T00:00:00Z"


@dataclass
class FakeResponse:
    """One scripted physical response (status/headers/body + behaviours)."""

    status: int
    headers: dict[str, str]
    body: bytes
    chunk_limit: int | None = None
    overdeliver: bool = False
    crash_after_bytes: int | None = None
    raise_after_bytes: int | None = None
    on_chunk: Callable[[int], None] | None = None
    _pos: int = 0
    closed: bool = False

    def read_chunk(self, max_bytes: int) -> bytes:
        if self.crash_after_bytes is not None and self._pos >= self.crash_after_bytes:
            raise SimulatedCrash("simulated process death mid-body")
        if self.raise_after_bytes is not None and self._pos >= self.raise_after_bytes:
            raise transport.TransportError("simulated connection reset", retryable=True)
        if self.on_chunk is not None:
            self.on_chunk(self._pos)
        size = max_bytes if not self.overdeliver else max_bytes + 4096
        if self.chunk_limit is not None:
            size = min(size, self.chunk_limit)
        chunk = self.body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


Override = Callable[[transport.TransportRequest, FakeResponse], FakeResponse]
Rule = Callable[[int, transport.TransportRequest, FakeResponse], FakeResponse]


def signed_url(file: str, suffix: str = "") -> str:
    token = hashlib.sha256(file.encode()).hexdigest()[:32]
    tail = f"/{suffix}" if suffix else ""
    return f"https://{frozen_v3.SIGNED_TARGET_HOST}{SIGNED_PREFIX}{token}{tail}?Signature=fake"


@dataclass
class FakeServer:
    """Honest synthetic origin: canonical 302 -> signed target -> exact 206."""

    files: dict[str, SyntheticFile]
    source: netpolicy.SourceIdentity = SYNTHETIC_SOURCE

    def respond(self, request: transport.TransportRequest) -> FakeResponse:
        checked = netpolicy.check_url(request.url)
        if checked.host == frozen_v3.CANONICAL_HOST:
            for f in self.files.values():
                if netpolicy.is_canonical_resource(checked, self.source, f.name):
                    token = hashlib.sha256(f.name.encode()).hexdigest()[:32]
                    location = (
                        f"https://{frozen_v3.SIGNED_TARGET_HOST}{SIGNED_PREFIX}{token}"
                        "?X-Xet-Signed-Range=bytes&Expires=9999999999&Signature=fake"
                    )
                    return FakeResponse(302, {"location": location}, b"Found. Redirecting")
            return FakeResponse(404, {}, b"not found")
        if checked.host == frozen_v3.SIGNED_TARGET_HOST and checked.path.startswith(SIGNED_PREFIX):
            token = checked.path[len(SIGNED_PREFIX) :].split("/")[0]
            for f in self.files.values():
                if hashlib.sha256(f.name.encode()).hexdigest()[:32] == token:
                    start, end = request.range_start, request.range_end
                    if end >= f.length:
                        return FakeResponse(416, {}, b"range not satisfiable")
                    body = f.content[start : end + 1]
                    return FakeResponse(
                        206,
                        {
                            "content-range": f"bytes {start}-{end}/{f.length}",
                            "content-length": str(len(body)),
                            "etag": f.etag,
                        },
                        body,
                    )
        return FakeResponse(404, {}, b"not found")


@dataclass
class FakeTransport:
    """Records every request spec; applies per-call scripted overrides."""

    server: FakeServer
    overrides: dict[int, Override] = field(default_factory=dict)
    calls: list[transport.TransportRequest] = field(default_factory=list)
    responses: list[FakeResponse] = field(default_factory=list)
    before_open: Callable[[int, transport.TransportRequest], None] | None = None
    rule: Rule | None = None

    def open(self, request: transport.TransportRequest) -> transport.TransportResponse:
        index = len(self.calls)
        self.calls.append(request)
        if self.before_open is not None:
            self.before_open(index, request)
        response = self.server.respond(request)
        if self.rule is not None:
            response = self.rule(index, request, response)
        override = self.overrides.get(index)
        if override is not None:
            response = override(request, response)
        self.responses.append(response)
        return response


# --------------------------------------------------------------------------
# Fixture writers (bindings probed from the actual runtime)
# --------------------------------------------------------------------------


def review_body(
    request_digest: str, *, decision: str = authorization.REVIEWER_DECISION
) -> dict[str, Any]:
    """A typed synthetic review decision bound to one authorization request."""
    body: dict[str, Any] = {
        "kind": authorization.REVIEW_KIND,
        "schema_version": authorization.REVIEW_SCHEMA_VERSION,
        "authorization_request_digest": request_digest,
        "epoch_id": frozen_v3.EPOCH_ID,
        "phase": "P",
        "decision": decision,
        "reviewer_identity": "synthetic-reviewer",
        "reviewed_at_utc": "2026-09-29T00:00:00Z",
        "review_report_sha256": hashlib.sha256(b"synthetic review report").hexdigest(),
    }
    body["digest"] = canonical.self_digest(body)
    return body


def authorization_review_bytes(body: dict[str, Any]) -> bytes:
    """Canonical typed review decision for this authorization's request digest."""
    return canonical.canonical_bytes(review_body(authorization.authorization_request_digest(body)))


def authorization_body(
    *,
    repo: Path,
    root: Path,
    plan_m: bytes,
    plan_t: bytes,
    review_bytes: bytes | None = None,
    implementation_commit: str = SYNTHETIC_COMMIT,
) -> dict[str, Any]:
    """An authorization whose runtime bindings come from the actual runtime.

    Without ``review_bytes`` the review binding is the typed review decision
    for this very request (``authorization_review_bytes``).
    """
    m = plan.validate_plan_bytes(plan_m, arm="M", synthetic=True)
    t = plan.validate_plan_bytes(plan_t, arm="T", synthetic=True)
    config = envidentity.worktree_identity(repo, envidentity.CONFIG_PATHS)
    body: dict[str, Any] = {
        "kind": authorization.AUTH_KIND,
        "schema_version": authorization.AUTH_SCHEMA_VERSION,
        "protocol_sha256": frozen_v3.PROTOCOL_SHA256,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
        "implementation_commit": implementation_commit,
        "child_manifest_digest": authorization.synthetic_manifest_digest(m.digest, t.digest),
        "epoch_id": frozen_v3.EPOCH_ID,
        "execution_root": fsroot.root_string(root),
        "phase": "P",
        "arms": ["M", "T"],
        "m_phase_p_plan_digest": m.digest,
        "t_phase_p_plan_digest": t.digest,
        "scientific_namespace": frozen_v3.SCIENTIFIC_NAMESPACE,
        "selection_digest": frozen_v3.SELECTION_DIGEST,
        "source_revision": frozen_v3.SOURCE_REVISION,
        "resource_caps": {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS},
        "resource_caps_digest": frozen_v3.RESOURCE_CAPS_DIGEST,
        "code_hashes": envidentity.worktree_identity(repo, envidentity.code_paths(repo)),
        "environment_identity": envidentity.runtime_environment(repo, config_identity=config),
        "reviewer_decision": authorization.REVIEWER_DECISION,
        "review_artifact_digest": "",
        "authorization_scope": authorization.AUTHORIZATION_SCOPE,
        "root_precondition": authorization.ROOT_PRECONDITION,
    }
    review = review_bytes if review_bytes is not None else authorization_review_bytes(body)
    body["review_artifact_digest"] = hashlib.sha256(review).hexdigest()
    body["digest"] = canonical.self_digest(body)
    return body


def approval_body(
    authorization_digest: str,
    *,
    decision: str = authorization.OPERATOR_DECISION,
    operator: str = "synthetic-operator",
    notes: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": authorization.APPROVAL_KIND,
        "schema_version": authorization.APPROVAL_SCHEMA_VERSION,
        "authorization_core_digest": authorization_digest,
        "epoch_id": frozen_v3.EPOCH_ID,
        "phase": "P",
        "decision": decision,
        "operator_identity": operator,
        "approved_at_utc": "2026-09-29T00:00:00Z",
        "notes": notes,
    }
    body["digest"] = canonical.self_digest(body)
    return body


def reseal(body: dict[str, Any]) -> bytes:
    """Recompute the self-digest after a deliberate (adversarial) edit."""
    sealed = {k: v for k, v in body.items() if k != "digest"}
    sealed["digest"] = canonical.self_digest(sealed)
    return canonical.canonical_bytes(sealed)


@dataclass
class SyntheticEpoch:
    """Everything a synthetic end-to-end run needs, written to disk."""

    repo: Path
    root: Path
    inputs: Path
    m_files: list[SyntheticFile]
    t_files: list[SyntheticFile]
    plan_m: bytes
    plan_t: bytes
    server: FakeServer
    transport: FakeTransport
    clock: FakeClock
    memory_reader: Callable[[], int] | None
    auth_path: Path
    approval_path: Path
    review_path: Path

    def harness(self) -> SyntheticHarness:
        return SyntheticHarness(
            root=self.root,
            plan_m_bytes=self.plan_m,
            plan_t_bytes=self.plan_t,
            transport=self.transport,
            implementation_commit=SYNTHETIC_COMMIT,
            clock=self.clock,
            memory_reader=self.memory_reader,
            sampling_interval_s=0.01,
        )

    def paths(self) -> EpochPaths:
        return {
            "repo_root": self.repo,
            "root": self.root,
            "authorization_path": self.auth_path,
            "approval_path": self.approval_path,
            "review_path": self.review_path,
        }


def constant_reader(value: int = 64 * 1024 * 1024) -> Callable[[], int]:
    return lambda: value


def build_epoch(
    repo: Path,
    workdir: Path,
    *,
    m_count: int = 2,
    t_count: int = 2,
    memory_reader: Callable[[], int] | None = None,
    t_data_ranges: int | None = None,
    use_real_sampler: bool = False,
) -> SyntheticEpoch:
    """Author a complete synthetic epoch (nothing created at the root yet)."""
    root = workdir / "root"
    inputs = workdir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    m_files = [make_m_file(i) for i in range(m_count)]
    t_files = [make_t_file(i) for i in range(t_count)]
    plan_m = plan_bytes("M", m_files)
    plan_t = plan_bytes("T", t_files, t_data_ranges=t_data_ranges)
    (inputs / "plan_m.json").write_bytes(plan_m)
    (inputs / "plan_t.json").write_bytes(plan_t)
    auth = authorization_body(repo=repo, root=root, plan_m=plan_m, plan_t=plan_t)
    review_path = inputs / "review.json"
    review_path.write_bytes(authorization_review_bytes(auth))
    auth_path = inputs / "authorization.json"
    auth_path.write_bytes(canonical.canonical_bytes(auth))
    approval_path = inputs / "approval.json"
    approval_path.write_bytes(canonical.canonical_bytes(approval_body(auth["digest"])))
    server = FakeServer({f.name: f for f in [*m_files, *t_files]})
    reader = None if use_real_sampler else (memory_reader or constant_reader())
    return SyntheticEpoch(
        repo=repo,
        root=root,
        inputs=inputs,
        m_files=m_files,
        t_files=t_files,
        plan_m=plan_m,
        plan_t=plan_t,
        server=server,
        transport=FakeTransport(server),
        clock=FakeClock(),
        memory_reader=reader,
        auth_path=auth_path,
        approval_path=approval_path,
        review_path=review_path,
    )


def reload_epoch(repo: Path, workdir: Path, *, use_real_sampler: bool = False) -> SyntheticEpoch:
    """Rebuild an authored epoch in another process from its saved inputs.

    The fixture files are regenerated deterministically and must reproduce
    the plan's strong ETags byte-for-byte, so the fake origin is identical.
    """
    inputs = workdir / "inputs"
    plan_m = (inputs / "plan_m.json").read_bytes()
    plan_t = (inputs / "plan_t.json").read_bytes()
    m = plan.validate_plan_bytes(plan_m, arm="M", synthetic=True)
    t = plan.validate_plan_bytes(plan_t, arm="T", synthetic=True)
    m_files = [make_m_file(i) for i in range(len(m.files))]
    t_files = [make_t_file(i) for i in range(len(t.files))]
    for built, planned in zip([*m_files, *t_files], [*m.files, *t.files], strict=True):
        if built.etag != planned.strong_etag or built.name != planned.file:
            raise RuntimeError("synthetic fixture regeneration is not byte-identical")
    server = FakeServer({f.name: f for f in [*m_files, *t_files]})
    return SyntheticEpoch(
        repo=repo,
        root=workdir / "root",
        inputs=inputs,
        m_files=m_files,
        t_files=t_files,
        plan_m=plan_m,
        plan_t=plan_t,
        server=server,
        transport=FakeTransport(server),
        clock=FakeClock(),
        memory_reader=None if use_real_sampler else constant_reader(),
        auth_path=inputs / "authorization.json",
        approval_path=inputs / "approval.json",
        review_path=inputs / "review.json",
    )
