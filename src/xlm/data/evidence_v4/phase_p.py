"""The v4.0 Phase-P engine: execute the frozen plan, one exact operation at a time.

Public entry points:

- :func:`run_live` — the only live path: THE committed plan (confirmed by its
  exact digest), THE frozen execution root and the live HTTPS transport;
- :func:`run_offline` — authored synthetic fixtures only: refuses the real
  repository/revision, the live transport and the frozen roots;
- :func:`inspect` — read-only status of a root.

None accepts a URL, file, range, ETag, operation kind or output path. Every
physical attempt is committed before its request; every received byte is
recorded; nothing is deleted. Any cap, identity, policy or consistency
violation is STOP: receipts are exported with status INCOMPLETE and the root
refuses further runs until reviewed (protocol sections 5-11).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, layout, state
from xlm.data.evidence_v4 import transport as tp

RECEIPT = "phase_p_receipt.json"
M_LAYOUT = "m_phase_p_layout.json"
T_LAYOUT = "t_phase_p_layout.json"
REQUESTS = "request_receipt.jsonl"
MANIFEST = "artifact_manifest.json"
READ_CHUNK = 65536
RESPONSE_READ_LIMIT = frozen.BODY_BYTES_PER_RESPONSE_MAX + frozen.OVERFLOW_DETECTION_BYTES
V3_ROOT = "G:\\Project\\xlm-evidence-v3\\essential-web"
_HEADERS = ("content-range", "etag", "content-encoding", "content-length", "location")
_MAGIC = {frozen.HEAD: "equal", frozen.M_FOOTER: "tail", frozen.T_TRAILER: "tail"}


class RefusedError(RuntimeError):
    """Refused before any Phase-P state change (digest, root, disk, lock, STOPPED root)."""


class StopError(RuntimeError):
    """Phase-P STOP: receipts preserved, run STOPPED, no reselection."""


@dataclass(frozen=True)
class Result:
    status: str  # COMPLETE | INCOMPLETE
    run_status: str
    stop_reason: str | None
    totals: dict[str, Any]
    root: str


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_digest(path: Path) -> tuple[int, str]:
    raw = path.read_bytes()
    return len(raw), _sha(raw)


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _root_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":  # directories cannot be opened for fsync on Windows
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(sink: BinaryIO, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = sink.write(view)
        view = view[written or 0 :]


def _same_or_inside(path: Path, fixed: str) -> bool:
    target = os.path.normcase(os.path.abspath(fixed))
    candidate = os.path.normcase(os.path.abspath(path))
    return candidate == target or candidate.startswith(target + os.sep)


class _Engine:
    def __init__(
        self,
        plan: frozen.Plan,
        root: Path,
        transport: tp.Transport,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.plan = plan
        self.root = root
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self._store: state.Store | None = None

    @property
    def store(self) -> state.Store:
        if self._store is None:
            raise state.StateError("store is not open")
        return self._store

    # -- top level ---------------------------------------------------------

    def run(self) -> Result:
        if _free_bytes(self.root) < frozen.FREE_DISK_BYTES_MIN:
            raise RefusedError("less than 1 GiB free on the execution root's drive")
        with self._open_store() as store:
            self._store = store
            run = store.run()
            if (
                run.protocol_version,
                run.plan_digest,
                run.selection_digest,
                run.source_revision,
                run.synthetic,
            ) != (
                frozen.PROTOCOL_VERSION,
                self.plan.digest,
                frozen.SELECTION_DIGEST,
                self.plan.source.revision,
                self.plan.synthetic,
            ):
                raise RefusedError("the root's run row binds a different protocol/plan/source")
            if run.status == "STOPPED":
                raise RefusedError(f"root is STOPPED ({run.stop_reason}); review required")
            try:
                self._reconcile()
                if run.status == "COMPLETE":
                    self._verify_exports()
                    return self._result()
                for op in self.plan.operations:
                    if self._is_complete(op):
                        continue
                    self._execute(op)
                layouts = self._layouts()
            except StopError as exc:
                store.set_status("STOPPED", str(exc), _utc())
                self._export(None)
                return self._result()
            store.set_status("COMPLETE", None, _utc())
            self._export(layouts)
            return self._result()

    def _open_store(self) -> state.Store:
        db = self.root / state.DB_NAME
        if not self.root.exists():
            self.root.mkdir(parents=True)
            create = True
        elif db.exists():
            create = False
        elif any(self.root.iterdir()):
            raise RefusedError(f"{self.root} is non-empty and has no {state.DB_NAME}")
        else:
            create = True
        try:
            store = state.Store(db, create=create)
        except state.StateError as exc:
            raise RefusedError(str(exc)) from exc
        try:
            if create:
                store.init_run(self.plan, _utc())
            store.run()
        except BaseException:
            store.close()
            raise
        (self.root / "tmp").mkdir(exist_ok=True)
        (self.root / "payload").mkdir(exist_ok=True)
        return store

    def _is_complete(self, op: frozen.Operation) -> bool:
        return op.op_id in self.store.outputs()

    # -- one logical operation -------------------------------------------

    def _execute(self, op: frozen.Operation) -> None:
        start, end = self._range(op)
        while True:
            used = self.store.tries_used(op.op_id)
            if used >= 1 + frozen.ADDITIONAL_TRIES_MAX:
                raise StopError(f"{op.op_id}: retries exhausted after {used} tries")
            if used >= 1:
                delays = frozen.RETRY_DELAYS_SECONDS
                self.sleep(delays[min(used, len(delays)) - 1])
            if self._logical_request(op, used + 1, start, end):
                return

    def _range(self, op: frozen.Operation) -> tuple[int, int]:
        if op.range is not None:
            return op.range
        trailer_op = self.plan.sibling(op, frozen.T_TRAILER)
        trailer = self._payload(trailer_op.op_id)
        try:
            start, end, _ = frozen.derive_t_footer_range(op, trailer)
        except frozen.PlanError as exc:
            raise StopError(str(exc)) from exc
        row = next(r for r in self.store.operations() if r["op_id"] == op.op_id)
        if (row["range_start"], row["range_end"]) != (start, end):
            raise StopError(f"{op.op_id}: stored derived range differs from the trailer rule")
        return start, end

    def _precheck(self, arm: str) -> None:
        count, body = self.store.arm_totals(arm)
        if count >= frozen.ATTEMPTS_PER_ARM_MAX:
            raise StopError(
                f"arm {arm}: physical attempt cap {frozen.ATTEMPTS_PER_ARM_MAX} reached"
            )
        if body + RESPONSE_READ_LIMIT > frozen.BODY_BYTES_PER_ARM_MAX:
            raise StopError(
                f"arm {arm}: {body} body bytes leave no room for one more response "
                f"under the {frozen.BODY_BYTES_PER_ARM_MAX}-byte arm cap"
            )
        used = _root_bytes(self.root)
        if used + RESPONSE_READ_LIMIT > frozen.ROOT_BYTES_MAX:
            raise StopError(
                f"execution root holds {used} bytes; one more response could exceed "
                f"the {frozen.ROOT_BYTES_MAX}-byte root cap"
            )

    def _finish(
        self, attempt_id: int, outcome: str, status: int | None, received: int, sha: str, error: str
    ) -> None:
        self.store.finish_attempt(
            attempt_id,
            outcome=outcome,
            http_status=status,
            received=received,
            sha256=sha,
            error=error[:2000],
            now=_utc(),
        )

    def _logical_request(self, op: frozen.Operation, try_number: int, start: int, end: int) -> bool:
        """One logical request (<=3 redirect transitions). True on verified success."""
        file = op.source_file.file
        try:
            current = tp.start_url(self.plan.source, file)
        except tp.PolicyError as exc:
            raise StopError(f"{op.op_id}: {exc}") from exc
        hop = 0
        while True:
            self._precheck(op.arm)
            attempt_id, temp_rel = self.store.begin_attempt(
                op=op,
                try_number=try_number,
                hop=hop,
                url=current.identity(),
                start=start,
                end=end,
                now=_utc(),
            )
            temp = self.root / temp_rel
            with open(temp, "xb", buffering=0) as sink:
                deadline = self.clock() + frozen.ATTEMPT_TIMEOUT_SECONDS
                try:
                    response = self.transport.open(
                        current.url,
                        start=start,
                        end=end,
                        timeout_seconds=frozen.ATTEMPT_TIMEOUT_SECONDS,
                        deadline=deadline,
                    )
                except tp.TransportError as exc:
                    self._finish(attempt_id, state.TRANSPORT_ERROR, None, 0, _sha(b""), str(exc))
                    return False
                except tp.PolicyError as exc:
                    self._finish(attempt_id, state.POLICY_REFUSED, None, 0, _sha(b""), str(exc))
                    raise StopError(f"{op.op_id}: {exc}") from exc
                try:
                    status = response.status
                    headers = {name: response.header(name) for name in _HEADERS}
                    received, digest, failure = self._stream(response, sink, attempt_id, deadline)
                finally:
                    response.close()
            status = status if type(status) is int else -1
            if failure is not None:
                outcome, message = failure
                self._finish(attempt_id, outcome, status, received, digest, message)
                if outcome == state.CAP_EXCEEDED:
                    raise StopError(f"{op.op_id}: {message}")
                return False
            if status in frozen.REDIRECT_STATUSES:
                if hop >= frozen.REDIRECT_TRANSITIONS_MAX:
                    message = f"redirect transition {hop + 1} exceeds the ceiling of 3"
                    self._finish(
                        attempt_id, state.POLICY_REFUSED, status, received, digest, message
                    )
                    raise StopError(f"{op.op_id}: {message}")
                try:
                    target = tp.resolve_redirect(
                        current, headers["location"], self.plan.source, file
                    )
                except tp.PolicyError as exc:
                    self._finish(
                        attempt_id, state.POLICY_REFUSED, status, received, digest, str(exc)
                    )
                    raise StopError(f"{op.op_id}: {exc}") from exc
                self._finish(attempt_id, state.REDIRECT, status, received, digest, "")
                current = target
                hop += 1
                continue
            if status in frozen.RETRYABLE_STATUSES:
                self._finish(attempt_id, state.HTTP_ERROR, status, received, digest, "retryable")
                return False
            if status != 206:
                message = f"non-retryable HTTP status {status}"
                self._finish(attempt_id, state.HTTP_ERROR, status, received, digest, message)
                raise StopError(f"{op.op_id}: {message}")
            body = temp.read_bytes()
            try:
                tp.verify_identity(
                    status=status,
                    headers=headers,
                    body=body,
                    final_url=current,
                    source=self.plan.source,
                    file=file,
                    expected=tp.Expected(
                        start=start,
                        end=end,
                        total_length=op.source_file.remote_length,
                        strong_etag=op.source_file.strong_etag,
                        magic=_MAGIC.get(op.kind, "none"),
                    ),
                )
            except tp.IdentityError as exc:
                self._finish(
                    attempt_id, state.IDENTITY_MISMATCH, status, received, digest, str(exc)
                )
                raise StopError(f"{op.op_id}: {exc}") from exc
            try:
                derived = self._verify_structure(op, body, start)
            except (layout.LayoutError, frozen.PlanError) as exc:
                self._finish(
                    attempt_id, state.VERIFICATION_FAILED, status, received, digest, str(exc)
                )
                raise StopError(f"{op.op_id}: {exc}") from exc
            retained = self.store.complete_operation(
                attempt_id, op=op, received=received, sha256=digest, now=_utc(), derived=derived
            )
            self._promote(temp, self.root / retained)
            return True

    def _stream(
        self, response: tp.Response, sink: BinaryIO, attempt_id: int, deadline: float
    ) -> tuple[int, str, tuple[str, str] | None]:
        """Stream the body to the temp file; durable byte counts at <=1 MiB intervals."""
        digest = hashlib.sha256()
        received = 0
        durable = 0
        failure: tuple[str, str] | None = None
        while True:
            if self.clock() >= deadline:
                failure = (state.TIMEOUT, "the 120-second attempt deadline elapsed")
                break
            try:
                chunk = response.read(min(READ_CHUNK, RESPONSE_READ_LIMIT - received))
            except tp.TransportError as exc:
                failure = (state.TRANSPORT_ERROR, str(exc))
                break
            if not chunk:
                break
            _write_all(sink, chunk)
            digest.update(chunk)
            received += len(chunk)
            if received >= RESPONSE_READ_LIMIT:
                failure = (
                    state.CAP_EXCEEDED,
                    f"response body exceeds {frozen.BODY_BYTES_PER_RESPONSE_MAX} bytes",
                )
                break
            if received - durable >= frozen.FLUSH_INTERVAL_BYTES:
                os.fsync(sink.fileno())
                self.store.record_bytes(attempt_id, received)
                durable = received
        os.fsync(sink.fileno())
        return received, digest.hexdigest(), failure

    def _verify_structure(
        self, op: frozen.Operation, body: bytes, start: int
    ) -> tuple[str, int, int] | None:
        """Kind-specific structural checks after identity; returns a derived T footer range."""
        if op.kind == frozen.M_FOOTER:
            assert op.source_file.m is not None
            if layout.trailer_length(body[-8:]) != op.expected_footer_length:
                raise layout.LayoutError("trailer footer length differs from the frozen L")
            layout.m_layout(body, footer_start=start, bindings=op.source_file.m)
        elif op.kind == frozen.T_TRAILER:
            footer_op = self.plan.sibling(op, frozen.T_FOOTER)
            derived_start, derived_end, _ = frozen.derive_t_footer_range(footer_op, body)
            return footer_op.op_id, derived_start, derived_end
        elif op.kind == frozen.T_FOOTER:
            assert op.source_file.t is not None
            trailer = self._payload(self.plan.sibling(op, frozen.T_TRAILER).op_id)
            layout.t_layout(body, trailer, footer_start=start, bindings=op.source_file.t)
        return None

    def _promote(self, temp: Path, target: Path) -> None:
        os.replace(temp, target)
        _fsync_dir(target.parent)

    def _payload(self, op_id: str) -> bytes:
        output = self.store.outputs().get(op_id)
        if output is None:
            raise StopError(f"{op_id} has no retained output")
        raw = (self.root / str(output["retained_file"])).read_bytes()
        if len(raw) != output["bytes"] or _sha(raw) != output["sha256"]:
            raise StopError(f"retained output of {op_id} does not match its recorded hash")
        return raw

    # -- restart reconciliation -------------------------------------------

    def _reconcile(self) -> None:
        plan_ops = self.plan.operations
        rows = self.store.operations()
        outputs = self.store.outputs()
        if [r["op_id"] for r in rows] != [o.op_id for o in plan_ops]:
            raise StopError("stored operations differ from the frozen plan (injected or missing)")
        for op, row in zip(plan_ops, rows, strict=True):
            fixed = (op.seq, op.arm, op.ordinal, op.source_file.file, op.kind)
            if (row["seq"], row["arm"], row["ordinal"], row["file"], row["kind"]) != fixed:
                raise StopError(f"{op.op_id}: stored operation differs from the frozen plan")
            if op.range is not None and (row["range_start"], row["range_end"]) != op.range:
                raise StopError(f"{op.op_id}: stored range differs from the frozen range")
            if (row["status"] == "COMPLETE") != (op.op_id in outputs):
                raise StopError(f"{op.op_id}: operation status and output rows disagree")
        for attempt in self.store.attempts():
            if attempt["outcome"] != state.IN_PROGRESS:
                continue
            self._check_temp_name(attempt)
            temp = self.root / attempt["temp_path"]
            if temp.exists():
                size, sha = _file_digest(temp)
                if size < attempt["response_bytes"]:
                    raise StopError(
                        f"attempt {attempt['attempt_id']}: temp file is smaller than its "
                        "durably recorded byte count"
                    )
            elif attempt["response_bytes"] > 0:
                raise StopError(f"attempt {attempt['attempt_id']}: recorded bytes but no temp file")
            else:  # died between the attempt commit and temp creation: retain an empty body
                temp.touch(exist_ok=False)
                size, sha = 0, _sha(b"")
            self.store.mark_interrupted(attempt["attempt_id"], size, sha, _utc())
        attempts = {a["attempt_id"]: a for a in self.store.attempts()}
        success = {o["attempt_id"]: op_id for op_id, o in outputs.items()}
        expected_temps: set[str] = set()
        for attempt in attempts.values():
            self._check_temp_name(attempt)
            if not 1 <= attempt["try_number"] <= 1 + frozen.ADDITIONAL_TRIES_MAX or not (
                0 <= attempt["hop"] <= frozen.REDIRECT_TRANSITIONS_MAX
            ):
                raise StopError(f"attempt {attempt['attempt_id']}: try/hop outside the limits")
            if (attempt["outcome"] == state.SUCCESS) != (attempt["attempt_id"] in success):
                raise StopError(f"attempt {attempt['attempt_id']}: success and outputs disagree")
            if attempt["outcome"] == state.SUCCESS:
                continue
            temp = self.root / attempt["temp_path"]
            if not temp.exists():
                raise StopError(f"attempt {attempt['attempt_id']}: retained body is missing")
            if _file_digest(temp) != (attempt["response_bytes"], attempt["response_sha256"]):
                raise StopError(f"attempt {attempt['attempt_id']}: retained body was altered")
            expected_temps.add(attempt["temp_path"])
        for op_id, output in outputs.items():
            attempt = attempts[output["attempt_id"]]
            if (
                attempt["op_id"] != op_id
                or output["retained_file"] != state.payload_name(op_id)
                or (attempt["response_bytes"], attempt["response_sha256"])
                != (output["bytes"], output["sha256"])
            ):
                raise StopError(f"{op_id}: output row disagrees with its attempt")
            payload = self.root / output["retained_file"]
            temp = self.root / attempt["temp_path"]
            if payload.exists():
                if temp.exists():
                    raise StopError(f"{op_id}: both payload and promoted temp file exist")
                if _file_digest(payload) != (output["bytes"], output["sha256"]):
                    raise StopError(f"{op_id}: retained output is corrupted")
            elif temp.exists() and _file_digest(temp) == (output["bytes"], output["sha256"]):
                self._promote(temp, payload)
            else:
                raise StopError(f"{op_id}: retained output is missing")
        expected_payloads = {o["retained_file"] for o in outputs.values()}
        for folder, expected in (("payload", expected_payloads), ("tmp", expected_temps)):
            for path in (self.root / folder).iterdir():
                if f"{folder}/{path.name}" not in expected:
                    raise StopError(f"unexpected file {folder}/{path.name} in the root")
        for op in plan_ops:
            if op.kind != frozen.T_FOOTER:
                continue
            row = next(r for r in rows if r["op_id"] == op.op_id)
            stored = (row["range_start"], row["range_end"])
            trailer_id = self.plan.sibling(op, frozen.T_TRAILER).op_id
            if trailer_id not in outputs:
                if stored != (None, None):
                    raise StopError(f"{op.op_id}: range present before its trailer completed")
                continue
            try:
                derived = frozen.derive_t_footer_range(op, self._payload(trailer_id))[:2]
            except frozen.PlanError as exc:
                raise StopError(str(exc)) from exc
            if stored != derived:
                raise StopError(f"{op.op_id}: stored range differs from the trailer rule")

    def _check_temp_name(self, attempt: dict[str, Any]) -> None:
        try:
            expected = state.temp_name(attempt["op_id"], attempt["attempt_id"])
        except state.StateError as exc:
            raise StopError(str(exc)) from exc
        if attempt["temp_path"] != expected:
            raise StopError(f"attempt {attempt['attempt_id']}: temp path is not the derived name")

    # -- outputs -----------------------------------------------------------

    def _file_entry(self, f: frozen.SourceFile) -> dict[str, Any]:
        outputs = self.store.outputs()
        ops = [o for o in self.plan.operations if o.arm == f.arm and o.ordinal == f.ordinal]
        return {
            "arm": f.arm,
            "ordinal": f.ordinal,
            "file": f.file,
            "remote_length": f.remote_length,
            "strong_etag": f.strong_etag,
            "canonical_url": self.plan.source.canonical_url(f.file),
            "payloads": {
                o.op_id: {k: outputs[o.op_id][k] for k in ("retained_file", "bytes", "sha256")}
                for o in ops
            },
        }

    def _layouts(self) -> tuple[dict[str, Any], dict[str, Any]]:
        m_files: list[dict[str, Any]] = []
        t_files: list[dict[str, Any]] = []
        try:
            for f in self.plan.files:
                entry = self._file_entry(f)
                if f.arm == "M":
                    assert f.m is not None
                    op = self.plan.operation(f"M-{f.ordinal:02d}-footer")
                    assert op.range is not None
                    entry["bindings"] = asdict(f.m)
                    entry["layout"] = layout.m_layout(
                        self._payload(op.op_id), footer_start=op.range[0], bindings=f.m
                    )
                    m_files.append(entry)
                else:
                    assert f.t is not None
                    op = self.plan.operation(f"T-{f.ordinal:02d}-footer")
                    trailer = self._payload(f"T-{f.ordinal:02d}-trailer")
                    start, end, length = frozen.derive_t_footer_range(op, trailer)
                    entry["bindings"] = asdict(f.t)
                    entry["validated_footer_length"] = length
                    entry["footer_range"] = [start, end]
                    entry["layout"] = layout.t_layout(
                        self._payload(op.op_id), trailer, footer_start=start, bindings=f.t
                    )
                    t_files.append(entry)
        except (layout.LayoutError, frozen.PlanError) as exc:
            raise StopError(f"layout reconstruction failed: {exc}") from exc
        return self._doc("m_phase_p_layout", files=m_files), self._doc(
            "t_phase_p_layout", files=t_files
        )

    def _doc(self, kind: str, **payload: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": f"essential_web_v4_{kind}",
            "protocol_version": frozen.PROTOCOL_VERSION,
            "protocol_sha256": frozen.PROTOCOL_SHA256,
            "freeze_digest": frozen.FREEZE_DIGEST,
            "plan_digest": self.plan.digest,
            "scientific_namespace": frozen.SCIENTIFIC_NAMESPACE,
            "selection_digest": frozen.SELECTION_DIGEST,
            "source": asdict(self.plan.source),
            "synthetic": self.plan.synthetic,
            **payload,
        }
        body["digest"] = canonical.self_digest(body)
        return body

    def _totals(self) -> dict[str, Any]:
        attempts = self.store.attempts()
        outputs = self.store.outputs()
        totals: dict[str, Any] = {}
        for arm in ("M", "T"):
            rows = [a for a in attempts if a["arm"] == arm]
            ops = [o for o in self.plan.operations if o.arm == arm]
            by_outcome: dict[str, int] = {}
            for a in rows:
                by_outcome[a["outcome"]] = by_outcome.get(a["outcome"], 0) + 1
            totals[arm] = {
                "logical_operations": len(ops),
                "logical_complete": sum(1 for o in ops if o.op_id in outputs),
                "physical_attempts": len(rows),
                "response_body_bytes": sum(a["response_bytes"] for a in rows),
                "attempts_by_outcome": dict(sorted(by_outcome.items())),
                "retained_payload_bytes": sum(
                    outputs[o.op_id]["bytes"] for o in ops if o.op_id in outputs
                ),
            }
        totals["all"] = {
            key: totals["M"][key] + totals["T"][key]
            for key in (
                "logical_operations",
                "logical_complete",
                "physical_attempts",
                "response_body_bytes",
                "retained_payload_bytes",
            )
        }
        return totals

    def _export(self, layouts: tuple[dict[str, Any], dict[str, Any]] | None) -> None:
        run = self.store.run()
        attempts = self.store.attempts()
        outputs = self.store.outputs()
        lines = b"".join(canonical.canonical_bytes(a) + b"\n" for a in attempts)
        canonical.write_atomic(self.root / REQUESTS, lines)
        layout_refs: dict[str, Any] | None = None
        superseded: list[str] = []
        if layouts is not None:
            layout_refs = {}
            for name, doc in ((M_LAYOUT, layouts[0]), (T_LAYOUT, layouts[1])):
                binding = canonical.write_canonical_json(self.root / name, doc)
                layout_refs[name] = {**binding, "digest": doc["digest"]}
        else:  # a root that was COMPLETE and later failed verification: keep, but demote
            for name in (M_LAYOUT, T_LAYOUT):
                if (self.root / name).exists():
                    os.replace(self.root / name, self.root / f"{name}.superseded")
                if (self.root / f"{name}.superseded").exists():
                    superseded.append(f"{name}.superseded")
        rows = {r["op_id"]: r for r in self.store.operations()}
        receipt = self._doc(
            "phase_p_receipt",
            status="COMPLETE" if run.status == "COMPLETE" else "INCOMPLETE",
            run_status=run.status,
            stop_reason=run.stop_reason,
            created_utc=run.created_utc,
            finalized_utc=_utc(),
            policy_digest=frozen.POLICY_DIGEST,
            source_files=[
                {
                    "arm": f.arm,
                    "ordinal": f.ordinal,
                    "file": f.file,
                    "remote_length": f.remote_length,
                    "strong_etag": f.strong_etag,
                    "canonical_url": self.plan.source.canonical_url(f.file),
                }
                for f in self.plan.files
            ],
            operations=[
                {
                    "op_id": op.op_id,
                    "seq": op.seq,
                    "arm": op.arm,
                    "ordinal": op.ordinal,
                    "kind": op.kind,
                    "range": (
                        None
                        if rows[op.op_id]["range_start"] is None
                        else [rows[op.op_id]["range_start"], rows[op.op_id]["range_end"]]
                    ),
                    "status": rows[op.op_id]["status"],
                    "output": outputs.get(op.op_id),
                }
                for op in self.plan.operations
            ],
            totals=self._totals(),
            request_receipt={
                "path": REQUESTS,
                "lines": len(attempts),
                "bytes": len(lines),
                "sha256": _sha(lines),
            },
            layouts=layout_refs,
            limits=frozen.LIMITS,
            phase_d="NOT PERFORMED",
        )
        canonical.write_canonical_json(self.root / RECEIPT, receipt)
        named = [REQUESTS, RECEIPT, *(sorted(layout_refs) if layout_refs else superseded)]
        retained = sorted(
            f"{folder}/{p.name}"
            for folder in ("payload", "tmp")
            for p in (self.root / folder).iterdir()
        )
        entries = {}
        for rel in [*retained, *named]:
            size, sha = _file_digest(self.root / rel)
            entries[rel] = {"bytes": size, "sha256": sha}
        manifest = self._doc(
            "artifact_manifest",
            status=receipt["status"],
            receipt_digest=receipt["digest"],
            artifacts=entries,
        )
        canonical.write_canonical_json(self.root / MANIFEST, manifest)

    def _verify_exports(self) -> None:
        try:
            manifest = canonical.loads_bytes_strict((self.root / MANIFEST).read_bytes())
            receipt = canonical.loads_bytes_strict((self.root / RECEIPT).read_bytes())
        except (OSError, canonical.CanonicalError) as exc:
            raise StopError(f"COMPLETE root exports are unreadable: {exc}") from exc
        for doc in (manifest, receipt):
            if canonical.self_digest(doc) != doc.get("digest") or doc.get("status") != "COMPLETE":
                raise StopError("COMPLETE root export self-digest/status mismatch")
            if doc.get("plan_digest") != self.plan.digest:
                raise StopError("COMPLETE root exports bind a different plan")
        if manifest["receipt_digest"] != receipt["digest"]:
            raise StopError("manifest does not bind the receipt")
        for rel, entry in manifest["artifacts"].items():
            path = self.root / rel
            if not path.is_file() or _file_digest(path) != (entry["bytes"], entry["sha256"]):
                raise StopError(f"COMPLETE root artifact {rel} drifted from its manifest")
        lines = b"".join(canonical.canonical_bytes(a) + b"\n" for a in self.store.attempts())
        if _sha(lines) != receipt["request_receipt"]["sha256"]:
            raise StopError("request receipt differs from the attempt table")

    def _result(self) -> Result:
        run = self.store.run()
        return Result(
            status="COMPLETE" if run.status == "COMPLETE" else "INCOMPLETE",
            run_status=run.status,
            stop_reason=run.stop_reason,
            totals=self._totals(),
            root=str(self.root),
        )


def live_root() -> Path:
    """THE frozen v4 execution root; never a caller input."""
    return Path(frozen.EXECUTION_ROOT)


def run_live(*, confirm_plan_digest: str) -> Result:
    """Execute THE frozen Phase-P plan against the live source at THE frozen root."""
    if confirm_plan_digest != frozen.PLAN_DIGEST:
        raise RefusedError("--confirm-plan-digest does not equal the frozen v4 plan digest")
    try:
        plan = frozen.load_committed_plan()
    except frozen.PlanError as exc:
        raise RefusedError(str(exc)) from exc
    if plan.digest != confirm_plan_digest or plan.synthetic:
        raise RefusedError("committed plan differs from the confirmed plan")
    root = live_root()
    engine = _Engine(plan, root, tp.LiveHttpsTransport(), sleep=time.sleep, clock=time.monotonic)
    return engine.run()


def run_offline(
    root: Path,
    *,
    plan: frozen.Plan,
    transport: tp.Transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    """Run a SYNTHETIC fixture plan through the same engine (tests only)."""
    if (
        not plan.synthetic
        or plan.source.repository != frozen.SYNTHETIC_REPOSITORY
        or plan.source.revision == frozen.SOURCE_REVISION
        or plan.digest == frozen.PLAN_DIGEST
    ):
        raise RefusedError("offline runs accept only synthetic fixture plans")
    if isinstance(transport, tp.LiveHttpsTransport):
        raise RefusedError("offline runs never use the live transport")
    if _same_or_inside(root, frozen.EXECUTION_ROOT) or _same_or_inside(root, V3_ROOT):
        raise RefusedError("offline runs never use the frozen v3/v4 execution roots")
    return _Engine(plan, root, transport, sleep=sleep, clock=clock).run()


def inspect(root: Path) -> dict[str, Any]:
    """Read-only summary of a root; never creates or modifies anything."""
    db = root / state.DB_NAME
    if not db.exists():
        return {"root": str(root), "status": "NOT_STARTED", "exists": root.exists()}
    conn = state.open_read_only(db)
    try:
        run = state.read_run(conn)
        attempts = state.read_attempts(conn)
        ops = [dict(r) for r in conn.execute("SELECT arm, status FROM operations")]
    except Exception as exc:
        raise RefusedError(f"state store is locked or unreadable: {exc}") from exc
    finally:
        conn.close()
    arms = {}
    for arm in ("M", "T"):
        rows = [a for a in attempts if a["arm"] == arm]
        arms[arm] = {
            "logical_operations": sum(1 for o in ops if o["arm"] == arm),
            "logical_complete": sum(
                1 for o in ops if o["arm"] == arm and o["status"] == "COMPLETE"
            ),
            "physical_attempts": len(rows),
            "response_body_bytes": sum(a["response_bytes"] for a in rows),
            "in_progress": sum(1 for a in rows if a["outcome"] == state.IN_PROGRESS),
        }
    return {
        "root": str(root),
        "status": run.status,
        "stop_reason": run.stop_reason,
        "plan_digest": run.plan_digest,
        "created_utc": run.created_utc,
        "updated_utc": run.updated_utc,
        "arms": arms,
    }
