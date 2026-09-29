"""The one trusted Phase-P execution boundary.

Supported entry points take only file paths (and, for disposable
synthetic epochs, a :class:`~xlm.data.evidence_v3.harness.SyntheticHarness`):

- :func:`check_authorization` — offline validation, touches no root.
- :func:`perform_genesis` — exclusive epoch genesis.
- :func:`execute_phase_p` — runs (or resumes) the authorized Phase-P plan.
- :func:`inspect_epoch` — read-only replay summary (no mutation).

Every call re-derives expectations from the actual repository/runtime,
re-parses the authorization, approval and review bytes, reloads and fully
re-validates ``epoch_start.json``, replays the hash-chained journal and
reconciles the physical root. Callers cannot pass validation results,
operations, URLs, ranges, labels, budgets or transports (REAL mode).

Per physical request the order is fixed:

    time hold (durable) -> ATTEMPT_RESERVE (durable, inside P ceilings
    that retain the future-D allocation) -> ATTEMPT_ISSUED (durable) ->
    single-hop transport with exact Range + absolute deadline ->
    ATTEMPT_HEADERS -> for each bounded chunk: supervisor checkpoint,
    ATTEMPT_BODY (durable) THEN expose bytes -> settle (never refunds).

Redirects come only from actual 3xx responses; each Location is policy-
checked and the next hop is a new durable reservation. Nothing is exposed
to identity checks, staging or parsing before it is journalled.
"""

from __future__ import annotations

import copy
import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    authorization,
    footer,
    frozen_v3,
    fsroot,
    genesis,
    journal,
    memory,
    netpolicy,
    plan,
    schedules,
    synthetic,
    transport,
)
from xlm.data.evidence_v3.harness import Clock, RealClock, SyntheticHarness


class PhasePError(RuntimeError):
    """Refusal before or outside physical work (no state change implied)."""


class PhasePStop(PhasePError):
    """Terminal fail-closed stop; the arm is durably P_INCOMPLETE."""


class _Retryable(Exception):
    pass


class _Stop(Exception):
    pass


CHUNK_BYTES = 65536
WORK_HOLD_NS = 30_000_000_000
MAX_INPUT_BYTES = 16 * 1024 * 1024
RESULT_KIND = "essential-web-evidence-v3-phase-p-result"


def _read_input(path: Path, what: str) -> bytes:
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise PhasePError(f"{what} unreadable: {exc}") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise PhasePError(f"{what} exceeds the input bound")
    return raw


@dataclass(frozen=True)
class _Context:
    repo: Path
    mode: str
    auth: authorization.ValidatedPhasePAuthorization
    approval: authorization.ValidatedOperatorApproval
    harness: SyntheticHarness | None


def _build_context(
    *,
    repo_root: Path,
    root: Path,
    authorization_path: Path,
    approval_path: Path,
    review_path: Path,
    harness: SyntheticHarness | None,
) -> _Context:
    if harness is not None:
        if type(harness) is not SyntheticHarness:
            raise PhasePError("harness must be a SyntheticHarness")
        if type(harness.transport) is not synthetic.FakeTransport:
            raise PhasePError("a synthetic harness may carry only the offline FakeTransport")
        if authorization.is_under_frozen_project(harness.root):
            raise PhasePError("synthetic harness roots may not touch the frozen project root")
    elif fsroot.root_string(root) != frozen_v3.EXECUTION_ROOT:
        raise PhasePError("REAL mode executes only at the frozen execution root")
    auth_raw = _read_input(authorization_path, "authorization")
    approval_raw = _read_input(approval_path, "operator approval")
    review_raw = _read_input(review_path, "review artifact")
    try:
        expect = authorization.derive_expectations(
            repo=repo_root,
            root=root,
            review_path=review_path,
            review_bytes=review_raw,
            harness=harness,
        )
        auth = authorization.validate_authorization(auth_raw, expect)
        approval = authorization.validate_operator_approval(approval_raw, auth)
    except authorization.AuthorizationError as exc:
        raise PhasePError(f"authorization refused: {exc}") from exc
    return _Context(
        repo=repo_root,
        mode=auth.mode,
        auth=auth,
        approval=approval,
        harness=harness,
    )


def check_authorization(
    *,
    repo_root: Path,
    root: Path,
    authorization_path: Path,
    approval_path: Path,
    review_path: Path,
    harness: SyntheticHarness | None = None,
) -> dict[str, Any]:
    """Offline validation only; never creates or reads the execution root."""
    ctx = _build_context(
        repo_root=repo_root,
        root=root,
        authorization_path=authorization_path,
        approval_path=approval_path,
        review_path=review_path,
        harness=harness,
    )
    return {
        "mode": ctx.mode,
        "authorization_digest": ctx.auth.digest,
        "operator_approval_digest": ctx.approval.digest,
        "implementation_commit": ctx.auth.implementation_commit,
        "child_manifest_digest": ctx.auth.child_manifest_digest,
        "m_phase_p_plan_digest": ctx.auth.plans["M"].digest,
        "t_phase_p_plan_digest": ctx.auth.plans["T"].digest,
        "execution_root": ctx.auth.execution_root,
    }


def _clock(harness: SyntheticHarness | None) -> Clock:
    return RealClock() if harness is None else harness.clock


def _interval(harness: SyntheticHarness | None) -> float:
    return memory.wait_interval() if harness is None else harness.sampling_interval_s


def perform_genesis(
    *,
    repo_root: Path,
    root: Path,
    authorization_path: Path,
    approval_path: Path,
    review_path: Path,
    harness: SyntheticHarness | None = None,
) -> dict[str, Any]:
    """Validate everything, then publish the exclusive epoch genesis."""
    ctx = _build_context(
        repo_root=repo_root,
        root=root,
        authorization_path=authorization_path,
        approval_path=approval_path,
        review_path=review_path,
        harness=harness,
    )
    clock = _clock(harness)
    try:
        root_ctx = genesis.verify_root(root, mode=ctx.mode, create=True)
        return genesis.publish(
            auth=ctx.auth,
            approval=ctx.approval,
            root_ctx=root_ctx,
            utc_now=clock.utc_now(),
            monotonic_ns=clock.monotonic_ns(),
            sampling_interval_s=_interval(harness),
        )
    except (genesis.GenesisError, fsroot.ContainmentError, journal.StateError, OSError) as exc:
        raise PhasePError(f"genesis refused: {exc}") from exc


# --------------------------------------------------------------------------
# Exclusive process lock (released by the OS on process death)
# --------------------------------------------------------------------------


_LOCK_OFFSET = 1 << 30


class _ProcessLock:
    def __init__(self, fs: fsroot.RootFS) -> None:
        self._fd: int | None = None
        path = fs._resolve(genesis.LOCK_REL)
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            if sys.platform == "win32":
                import msvcrt

                # Lock a byte far past EOF so the (empty) lock file stays readable.
                os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise PhasePError("another process holds the epoch lock") from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, _LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


class _BoundedBuffer:
    """Body buffer that can never grow beyond its fixed capacity."""

    def __init__(self, capacity: int) -> None:
        self._buf = bytearray()
        self._capacity = capacity

    def extend(self, chunk: bytes) -> None:
        if len(self._buf) + len(chunk) > self._capacity:
            raise _Stop("body exceeds its bounded buffer")
        self._buf.extend(chunk)

    def value(self) -> bytes:
        return bytes(self._buf)


class _Session:
    """One locked, validated, supervised execution session."""

    def __init__(self, ctx: _Context, root: Path) -> None:
        self.ctx = ctx
        self.clock = _clock(ctx.harness)
        self.interval = _interval(ctx.harness)
        self.transport: transport.Transport = (
            transport._LiveHttpsTransport() if ctx.harness is None else ctx.harness.transport
        )
        try:
            self.root_ctx = genesis.verify_root(root, mode=ctx.mode, create=False)
        except (genesis.GenesisError, fsroot.ContainmentError, OSError) as exc:
            raise PhasePError(f"execution root refused: {exc}") from exc
        if self.root_ctx.identity.path != ctx.auth.execution_root:
            raise PhasePError("execution root differs from the authorized root")
        self.fs = fsroot.RootFS(self.root_ctx.identity)
        if not self.fs.exists(genesis.EPOCH_START_REL):
            raise PhasePError("no epoch genesis at this root: network gate closed")
        self.lock = _ProcessLock(self.fs)
        self.supervisor: memory.Supervisor | None = None
        self.log: journal.Journal | None = None
        try:
            self._open()
        except (fsroot.ContainmentError, journal.JournalError, journal.StateError, OSError) as exc:
            self.close()
            raise PhasePError(f"epoch open refused: {exc}") from exc
        except BaseException:
            self.close()
            raise

    # ----- opening ---------------------------------------------------------

    def _open(self) -> None:
        try:
            start = genesis.load_epoch_start(
                self.fs,
                auth=self.ctx.auth,
                approval=self.ctx.approval,
                root_ctx=self.root_ctx,
                sampling_interval_s=self.interval,
            )
        except genesis.GenesisError as exc:
            raise PhasePError(f"epoch_start refused: {exc}") from exc
        self.start_digest = str(start["digest"])
        self.log = journal.Journal(
            self.fs,
            epoch_id=frozen_v3.EPOCH_ID,
            seed=journal.chain_seed(frozen_v3.EPOCH_ID, self.start_digest),
        )
        try:
            records = self.log.load()
            if records[0]["body"]["epoch_start_digest"] != self.start_digest:
                raise PhasePError("journal GENESIS binds a different epoch_start")
            self.state = journal.new_state(records[0]["body"], frozen_v3.EPOCH_ID)
            for record in records:
                self.state.apply(record)
        except (journal.JournalError, journal.StateError, KeyError, TypeError) as exc:
            raise PhasePError(f"journal refused: {exc}") from exc
        expected_plans = {arm: genesis.plan_summary(self.ctx.auth.plans[arm]) for arm in ("M", "T")}
        if canonical.canonical_bytes(self.state.plans) != canonical.canonical_bytes(expected_plans):
            raise PhasePError("journal plan summaries differ from the authorized plans")
        self._recover()
        self._reconcile(verify_hashes=True)
        reader = (
            memory.TreeSampler(memory.OwnedProcessRegistry())
            if self.ctx.harness is None or self.ctx.harness.memory_reader is None
            else self.ctx.harness.memory_reader
        )
        self.supervisor = memory.Supervisor(
            reader=reader,
            cap_bytes=frozen_v3.MEMORY_RESIDENT_BYTES_MAX,
            interval_s=self.interval,
            monotonic_ns=self.clock.monotonic_ns,
        )
        try:
            self.supervisor.start()
        except memory.MemoryGuardError as exc:
            raise PhasePError(f"memory supervision unavailable: {exc}") from exc
        self.session_id = secrets.token_hex(12)
        self._mark = self.clock.monotonic_ns()
        self._commit(
            "SESSION_OPEN",
            {
                "session": self.session_id,
                "pid": os.getpid(),
                "mode": self.ctx.mode,
                "utc": self.clock.utc_now(),
            },
        )
        self.targets: dict[str, str] = {}

    def _commit(self, kind: str, body: dict[str, Any]) -> None:
        """Validate on a trial copy, append durably, then adopt the state."""
        assert self.log is not None
        record = {"seq": self.state.seq + 1, "type": kind, "body": body}
        trial = _clone(self.state)
        trial.apply(record)
        self.log.append(kind, body)
        self.state = trial

    def _recover(self) -> None:
        """Conserve a dead session's open work; never refund."""
        dead = self.state.session
        if dead is None:
            return
        if self.state.open_attempt is not None:
            attempt = self.state.attempts[self.state.open_attempt]
            self._commit(
                "CRASH_ATTEMPT",
                {
                    "attempt_id": attempt.attempt_id,
                    "charged": max(attempt.reservation, attempt.body_total),
                },
            )
        self._resolve_pending("CRASH")
        for arm_name, arm in self.state.arms.items():
            if arm.hold_ns:
                self._commit("CRASH_TIME", {"arm": arm_name, "charged_ns": arm.hold_ns})
        self._commit("SESSION_CLOSE", {"session": dead, "reason": "crash-recovered"})

    def _resolve_pending(self, mode: str) -> None:
        """Settle reserved writes from physical evidence (verified bytes only)."""
        for rel, pend in list(self.state.pending.items()):
            committed = False
            if self.fs.exists(rel):
                matches = self.fs.size(rel) == pend.size
                if matches:
                    data = self.fs.read_bytes(rel, max_bytes=pend.size)
                    matches = hashlib.sha256(data).hexdigest() == pend.sha256
                if matches:
                    committed = True
                elif not pend.replaces:
                    raise PhasePError(f"{rel} holds unexpected bytes: BLOCKED for review")
            if self.fs.exists(pend.temp_rel):
                self.fs.remove(pend.temp_rel)
            if mode == "CRASH":
                action = "committed" if committed else "aborted"
                self._commit("CRASH_DISK", {"rel": rel, "action": action})
            elif committed:
                self._commit("DISK_COMMIT", {"rel": rel, "size": pend.size, "sha256": pend.sha256})
            else:
                self._commit("DISK_ABORT", {"rel": rel, "reason": "write failed; temp removed"})

    def _reconcile(self, *, verify_hashes: bool = False) -> None:
        """Physical scan must equal durable inventory; unknown objects STOP."""
        physical = self.fs.scan()
        allowed_temps = {p.temp_rel: p.size for p in self.state.pending.values()}
        pending_finals = {p.rel: p.size for p in self.state.pending.values()}
        for rel, size in physical.items():
            entry = self.state.disk.get(rel)
            if entry is not None:
                if entry.control_allowance:
                    if size > entry.size:
                        raise PhasePError(f"control file {rel} exceeds its reserved allowance")
                elif size != entry.size and rel not in pending_finals:
                    raise PhasePError(f"{rel} size {size} differs from inventory {entry.size}")
            elif rel in allowed_temps or rel in pending_finals:
                if size > max(allowed_temps.get(rel, 0), pending_finals.get(rel, 0)):
                    raise PhasePError(f"pending {rel} exceeds its reservation")
            else:
                raise PhasePError(f"unknown physical file in the execution root: {rel}")
        for rel, entry in self.state.disk.items():
            if entry.control_allowance:
                continue
            if rel not in physical:
                raise PhasePError(f"inventoried file {rel} is missing")
            if verify_hashes and entry.sha256 is not None:
                data = self.fs.read_bytes(rel, max_bytes=entry.size)
                if hashlib.sha256(data).hexdigest() != entry.sha256:
                    raise PhasePError(f"inventoried file {rel} bytes changed")

    def close(self) -> None:
        if self.supervisor is not None:
            self.supervisor.stop()
        if self.log is not None:
            self.log.close()
        self.lock.release()

    # ----- runtime ---------------------------------------------------------

    def _time(self, arm: str, *, step: str, hold_file: str | None, hold_ns: int) -> int:
        """Durably charge elapsed time, then open the next step's hold."""
        now = self.clock.monotonic_ns()
        delta = max(0, now - self._mark)
        self._mark = now
        self._commit(
            "TIME",
            {
                "arm": arm,
                "delta_ns": delta,
                "hold_file": hold_file,
                "hold_ns": hold_ns,
                "step": step,
            },
        )
        return now

    def _remaining_ns(self, arm: str, file: str | None) -> int:
        """Remaining time for a new step, counting the still-uncharged interval."""
        st = self.state.arms[arm]
        seconds = self.ctx.auth.plans[arm].time_seconds
        pending = max(0, self.clock.monotonic_ns() - self._mark)
        remaining = seconds["arm"] * 1_000_000_000 - st.time_ns - pending
        if file is not None:
            used = st.time_ns_by_file.get(file, 0)
            if st.hold_file == file:
                used += pending
            remaining = min(remaining, seconds["file"] * 1_000_000_000 - used)
        return remaining

    def _hold(self, arm: str, file: str | None, *, step: str, cap_ns: int) -> int:
        remaining = self._remaining_ns(arm, file)
        hold = min(cap_ns, remaining)
        if hold <= 0:
            self._time(arm, step="close", hold_file=None, hold_ns=0)
            raise _Stop(f"runtime budget exhausted for arm {arm} file {file}")
        now = self._time(arm, step=step, hold_file=file, hold_ns=hold)
        assert self.supervisor is not None
        self.supervisor.set_deadline(now + hold)
        return now + hold

    # ----- run -------------------------------------------------------------

    def run(self, max_operations: int | None) -> dict[str, Any]:
        try:
            return self._run(max_operations)
        except journal.JournalError:
            raise
        except Exception:
            self._close_if_quiescent("refused")
            raise

    def _close_if_quiescent(self, reason: str) -> None:
        st = self.state
        if (
            st.session == self.session_id
            and st.open_attempt is None
            and not st.pending
            and not any(a.hold_ns for a in st.arms.values())
        ):
            self._commit("SESSION_CLOSE", {"session": self.session_id, "reason": reason})

    def _run(self, max_operations: int | None) -> dict[str, Any]:
        executed = 0
        stopped_early = False
        for arm in ("M", "T"):
            st = self.state.arms[arm]
            if st.phase == "P_COMPLETE_SEALED":
                continue
            if st.phase == "P_INCOMPLETE":
                raise PhasePStop(f"arm {arm} is P_INCOMPLETE; no automatic rerun")
            p = self.ctx.auth.plans[arm]
            if st.phase == "NOT_STARTED":
                if max_operations is not None and executed >= max_operations:
                    stopped_early = True
                    break
                self._commit(
                    "PHASE", {"arm": arm, "to": "P_RUNNING", "reason": None, "result_digest": None}
                )
            try:
                while self.state.next_op(arm) < len(p.operations):
                    if max_operations is not None and executed >= max_operations:
                        stopped_early = True
                        break
                    self._run_operation(p.operations[self.state.next_op(arm)])
                    executed += 1
                if stopped_early:
                    self._time(arm, step="close", hold_file=None, hold_ns=0)
                    break
                self._seal(arm)
            except journal.JournalError:
                raise
            except Exception as exc:
                # Fail closed: any refusal or unexpected error ends this arm's
                # Phase P durably as P_INCOMPLETE, conserving every charge.
                self._fail_arm(arm, f"{type(exc).__name__}: {exc}")
                raise PhasePStop(f"arm {arm} STOPPED: {exc}") from exc
        self._commit("SESSION_CLOSE", {"session": self.session_id, "reason": "quiescent"})
        return {
            "executed_operations": executed,
            "stopped_early": stopped_early,
            "phases": {arm: s.phase for arm, s in self.state.arms.items()},
            "result_digests": {arm: s.result_digest for arm, s in self.state.arms.items()},
        }

    def _fail_arm(self, arm: str, reason: str) -> None:
        if self.state.open_attempt is not None:
            attempt = self.state.attempts[self.state.open_attempt]
            self._commit(
                "ATTEMPT_SETTLE",
                {
                    "attempt_id": attempt.attempt_id,
                    "outcome": "FAILED",
                    "charged": max(attempt.reservation, attempt.body_total),
                    "reason": "open at failure; reservation retained",
                },
            )
        self._resolve_pending("FAIL")
        if self.state.arms[arm].phase == "P_RUNNING":
            self._time(arm, step="close", hold_file=None, hold_ns=0)
            self._commit("ABORT", {"arm": arm, "reason": reason[:500]})
            self._commit(
                "PHASE",
                {"arm": arm, "to": "P_INCOMPLETE", "reason": reason[:500], "result_digest": None},
            )
        self._commit("SESSION_CLOSE", {"session": self.session_id, "reason": "stopped"})

    # ----- one logical operation ------------------------------------------

    def _run_operation(self, op: plan.PlanOperation) -> None:
        prior = self.state.attempts_for(op.arm, op.index)
        logical = max((a.logical_attempt for a in prior), default=-1) + 1
        if op.range is not None:
            span = op.range
        else:
            previous = self.state.arms[op.arm].ops_done[op.index - 1]
            rng = previous.get("next_range")
            if not isinstance(rng, list):
                raise _Stop("derived footer range missing")
            span = (int(rng[0]), int(rng[1]))
        while True:
            if logical > frozen_v3.MAX_RETRIES:
                raise _Stop(f"{op.file} {op.op}: retries exhausted")
            if logical > 0:
                delay = frozen_v3.RETRY_DELAYS_SECONDS[min(logical, 2) - 1]
                self._hold(op.arm, op.file, step="backoff", cap_ns=(delay + 1) * 1_000_000_000)
                self.clock.sleep(float(delay))
            try:
                body, facts = self._fetch(op, span, logical)
                break
            except _Retryable:
                logical += 1
        self._stage(op, body, facts)

    def _fetch(
        self, op: plan.PlanOperation, span: tuple[int, int], logical: int
    ) -> tuple[bytes, dict[str, Any]]:
        p = self.ctx.auth.plans[op.arm]
        start, end = span
        length = end - start + 1
        if length >= p.response_body_bytes_max:
            raise _Stop(f"{op.file}: range of {length} bytes exceeds the response cap")
        reservation = schedules.body_reservation(length)
        cached = self.targets.get(op.file)
        url = cached if cached is not None else p.source.canonical_url(op.file)
        kind = "signed_target" if cached is not None else "canonical"
        used_cache = cached is not None
        hops = 0
        while True:
            deadline = self._hold(
                op.arm,
                op.file,
                step="request",
                cap_ns=frozen_v3.REQUEST_SECONDS_MAX * 1_000_000_000,
            )
            checked = netpolicy.check_url(url)
            attempt_id = f"{op.arm}-o{op.index:03d}-l{logical}-h{hops}-s{self.state.sessions}"
            try:
                self._commit(
                    "ATTEMPT_RESERVE",
                    {
                        "attempt_id": attempt_id,
                        "arm": op.arm,
                        "file": op.file,
                        "op": op.op,
                        "op_index": op.index,
                        "logical_attempt": logical,
                        "hop": hops,
                        "target_kind": kind,
                        "host": checked.host,
                        "range": [start, end],
                        "reservation": reservation,
                        "category": op.category,
                    },
                )
            except journal.StateError as exc:
                self._time(op.arm, step="close", hold_file=None, hold_ns=0)
                raise _Stop(f"reservation refused: {exc}") from exc
            self._commit("ATTEMPT_ISSUED", {"attempt_id": attempt_id})
            remaining_s = (deadline - self.clock.monotonic_ns()) / 1e9
            request = None
            if remaining_s > 0:
                request = transport._make_request(
                    url=url,
                    range_start=start,
                    range_end=end,
                    deadline_ns=deadline,
                    timeout_seconds=min(float(frozen_v3.REQUEST_SECONDS_MAX), remaining_s),
                    max_read_bytes=reservation,
                    attempt_id=attempt_id,
                )
                transport._mark_issued(request)
            outcome = self._one_hop(op, request, attempt_id, reservation, length)
            status, headers, body = outcome
            if status in netpolicy.REDIRECT_STATUSES:
                try:
                    location, kind = netpolicy.check_redirect(
                        headers.get("location"),
                        source=p.source,
                        file=op.file,
                        transitions_so_far=hops,
                    )
                except netpolicy.PolicyError as exc:
                    self._settle(attempt_id, "REFUSED", len(body), None, f"redirect refused: {exc}")
                    raise _Stop(f"{op.file}: redirect refused: {exc}") from exc
                self._settle(attempt_id, "REDIRECT", len(body), None, "redirect followed")
                url = location.url
                hops += 1
                continue
            if status == 206:
                try:
                    netpolicy.verify_identity(
                        status=status,
                        headers=headers,
                        body=body,
                        final_url=checked,
                        final_kind=kind,
                        source=p.source,
                        file=op.file,
                        expected=netpolicy.ExpectedIdentity(
                            start=start,
                            end=end,
                            total_length=op.remote_length,
                            strong_etag=op.strong_etag,
                            magic=op.magic,
                        ),
                    )
                    facts = self._facts(op, body, start, end)
                except (netpolicy.IdentityError, plan.PlanError, footer.FooterError) as exc:
                    self._settle(attempt_id, "FAILED", len(body), None, f"identity STOP: {exc}")
                    raise _Stop(f"{op.file}: identity STOP: {exc}") from exc
                self._settle(
                    attempt_id, "COMPLETE", len(body), hashlib.sha256(body).hexdigest(), "verified"
                )
                if kind == "signed_target":
                    self.targets[op.file] = url
                return body, facts
            self._settle(attempt_id, "FAILED", len(body), None, f"status {status}")
            if status in frozen_v3.RETRYABLE_HTTP:
                raise _Retryable()
            if used_cache and hops == 0 and status in (403, 404, 410):
                self.targets.pop(op.file, None)
                raise _Retryable()
            raise _Stop(f"{op.file}: unexpected HTTP status {status}")

    def _settle(
        self, attempt_id: str, outcome: str, charged: int, sha: str | None, reason: str
    ) -> None:
        body: dict[str, Any] = {
            "attempt_id": attempt_id,
            "outcome": outcome,
            "charged": charged,
            "reason": reason[:300],
        }
        if sha is not None:
            body["body_sha256"] = sha
        self._commit("ATTEMPT_SETTLE", body)

    def _uncertain(self, attempt_id: str, reason: str) -> None:
        attempt = self.state.attempts[attempt_id]
        self._settle(
            attempt_id, "FAILED", max(attempt.reservation, attempt.body_total), None, reason
        )

    def _one_hop(
        self,
        op: plan.PlanOperation,
        request: transport.TransportRequest | None,
        attempt_id: str,
        reservation: int,
        payload_length: int,
    ) -> tuple[int, dict[str, str], bytes]:
        """Issue one physical request and stream its body through accounting."""
        assert self.supervisor is not None
        if request is None:
            self._uncertain(attempt_id, "deadline passed before issue")
            raise _Retryable()
        try:
            self.supervisor.checkpoint()
            response = self.transport.open(request)
        except memory.MemoryGuardError as exc:
            self._uncertain(attempt_id, f"memory abort: {exc}")
            raise _Stop(f"memory supervision abort: {exc}") from exc
        except memory.StepDeadlineExceeded as exc:
            self._uncertain(attempt_id, "deadline exceeded before response")
            raise _Retryable() from exc
        except transport.TransportError as exc:
            self._uncertain(attempt_id, f"transport failure: {exc}")
            if exc.retryable:
                raise _Retryable() from exc
            raise _Stop(f"transport refused: {exc}") from exc
        except Exception as exc:
            self._uncertain(attempt_id, f"transport raised {type(exc).__name__}")
            raise _Stop(f"transport raised {type(exc).__name__}: {exc}") from exc
        try:
            status = response.status
            raw_headers = response.headers
            if type(status) is not int or not 100 <= status <= 599:
                raise _Stop("transport returned an invalid status")
            headers: dict[str, str] = {}
            for name, value in dict(raw_headers).items():
                if type(name) is not str or type(value) is not str or name != name.lower():
                    raise _Stop("transport returned non-normalized headers")
                headers[name] = value
            location = headers.get("location")
            location_host = None
            if location is not None:
                try:
                    location_host = netpolicy.check_url(location).host
                except netpolicy.PolicyError:
                    location_host = "REFUSED"
            self._commit(
                "ATTEMPT_HEADERS",
                {
                    "attempt_id": attempt_id,
                    "status": status,
                    "content_range": headers.get("content-range"),
                    "content_length": headers.get("content-length"),
                    "etag": headers.get("etag"),
                    "location_host": location_host,
                },
            )
            limit = payload_length if status == 206 else schedules.NON_PAYLOAD_BODY_BYTES
            body = self._stream(response, attempt_id, limit, reservation)
            return status, headers, body
        except _Stop:
            if self.state.open_attempt == attempt_id:
                self._uncertain(attempt_id, "stopped while reading")
            raise
        except _Retryable:
            raise
        except memory.MemoryGuardError as exc:
            self._uncertain(attempt_id, f"memory abort while reading: {exc}")
            raise _Stop(f"memory supervision abort: {exc}") from exc
        except memory.StepDeadlineExceeded as exc:
            self._uncertain(attempt_id, "deadline exceeded while reading")
            raise _Retryable() from exc
        except transport.TransportError as exc:
            self._uncertain(attempt_id, f"body read failure: {exc}")
            if exc.retryable:
                raise _Retryable() from exc
            raise _Stop(f"transport refused: {exc}") from exc
        except journal.JournalError:
            raise
        except Exception as exc:
            if self.state.open_attempt == attempt_id:
                self._uncertain(attempt_id, f"reader raised {type(exc).__name__}")
            raise _Stop(f"response reader raised {type(exc).__name__}: {exc}") from exc
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _stream(self, response: Any, attempt_id: str, limit: int, reservation: int) -> bytes:
        """Meter every chunk durably BEFORE exposing it; never exceed limit."""
        assert self.supervisor is not None
        buffer = _BoundedBuffer(limit)
        total = 0
        while True:
            self.supervisor.checkpoint()
            want = min(CHUNK_BYTES, limit - total + 1, reservation - total)
            chunk = response.read_chunk(want)
            if type(chunk) is not bytes:
                raise _Stop("transport returned a non-bytes chunk")
            if not chunk:
                return buffer.value()
            total += len(chunk)
            self._commit("ATTEMPT_BODY", {"attempt_id": attempt_id, "total": total})
            if len(chunk) > want or total > limit:
                self._uncertain(attempt_id, f"oversized body ({total} > {limit}) refused")
                raise _Stop(f"response body exceeds its bound ({total} > {limit})")
            buffer.extend(chunk)

    def _facts(self, op: plan.PlanOperation, body: bytes, start: int, end: int) -> dict[str, Any]:
        facts: dict[str, Any] = {"range": [start, end], "bytes": len(body)}
        if op.op == "M_FOOTER_AND_TRAILER":
            length = footer.trailer_length(body[-8:])
            if length != op.expected_footer_length or start != op.remote_length - 8 - length:
                raise footer.FooterError(
                    f"trailer footer length {length} contradicts the frozen footer start"
                )
            facts["footer_length"] = length
        elif op.op == "T_TRAILER":
            derived = plan.derive_t_footer_range(
                plan.PlanOperation(
                    arm=op.arm,
                    index=op.index + 1,
                    file_ordinal=op.file_ordinal,
                    file=op.file,
                    op="T_FOOTER_FROM_TRAILER",
                    range=None,
                    remote_length=op.remote_length,
                    strong_etag=op.strong_etag,
                    expected_footer_length=None,
                ),
                body,
            )
            footer_bytes = derived[1] - derived[0] + 1
            ceiling = self.ctx.auth.plans[op.arm].file_plan(op.file).ceiling
            used = self.state.arms[op.arm].body_charged_by_file.get(op.file, 0)
            if footer_bytes >= frozen_v3.RESPONSE_BODY_BYTES_MAX or (
                used + len(body) + schedules.body_reservation(footer_bytes)
                > ceiling["footer_bytes"]
            ):
                raise footer.FooterError(
                    f"footer length {footer_bytes} cannot fit the remaining P footer budget"
                )
            facts["footer_length"] = footer_bytes
            facts["next_range"] = [derived[0], derived[1]]
        return facts

    # ----- the single accounted write API --------------------------------

    def _write(
        self,
        rel: str,
        data: bytes,
        *,
        arms: list[str],
        category: str = "scratch",
        op_complete: dict[str, Any] | None = None,
    ) -> None:
        """Reserve (incl. old+new peak) -> write temp -> rename -> reconcile -> commit."""
        parent = rel.rsplit("/", 1)[0] if "/" in rel else None
        if parent is not None:
            self.fs.mkdirs(parent)
        sha = hashlib.sha256(data).hexdigest()
        temp_rel = rel + ".tmp"
        replaces = rel in self.state.disk
        body: dict[str, Any] = {
            "rel": rel,
            "temp_rel": temp_rel,
            "category": category,
            "arms": arms,
            "size": len(data),
            "sha256": sha,
            "replaces": replaces,
            "op_complete": op_complete,
        }
        try:
            self._commit("DISK_RESERVE", body)
        except journal.StateError as exc:
            raise _Stop(f"disk reservation refused: {exc}") from exc
        try:
            self.fs.create_exclusive(temp_rel, data)
            self.fs.rename(temp_rel, rel, replace=replaces)
            if self.fs.size(rel) != len(data):
                raise _Stop(f"{rel}: written size differs from its reservation")
            self._reconcile()
        except (fsroot.ContainmentError, OSError, PhasePError) as exc:
            raise _Stop(f"disk write refused: {exc}") from exc
        self._commit("DISK_COMMIT", {"rel": rel, "size": len(data), "sha256": sha})

    def _stage(self, op: plan.PlanOperation, body: bytes, facts: dict[str, Any]) -> None:
        self._hold(op.arm, op.file, step="work", cap_ns=WORK_HOLD_NS)
        rel = (
            f"phase_p/{op.arm.lower()}/f{op.file_ordinal:02d}-{op.op.lower().replace('_', '-')}.bin"
        )
        op_complete = {
            "arm": op.arm,
            "op_index": op.index,
            "file": op.file,
            "op": op.op,
            "staged": rel,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "facts": {k: v for k, v in facts.items() if k != "next_range"},
            "next_range": facts.get("next_range"),
        }
        self._write(rel, body, arms=[op.arm], op_complete=op_complete)

    # ----- seal -------------------------------------------------------------

    def _seal(self, arm: str) -> None:
        assert self.supervisor is not None
        self._hold(arm, None, step="work", cap_ns=WORK_HOLD_NS)
        p = self.ctx.auth.plans[arm]
        done = self.state.arms[arm].ops_done
        files: list[dict[str, Any]] = []
        for fp in p.files:
            cells = [c for c in done if c["file"] == fp.file]
            staged = {
                c["op"]: self.fs.read_bytes(
                    c["staged"], max_bytes=frozen_v3.RESPONSE_BODY_BYTES_MAX
                )
                for c in cells
            }
            for c in cells:
                if hashlib.sha256(staged[c["op"]]).hexdigest() != c["body_sha256"]:
                    raise _Stop(f"staged bytes for {fp.file} changed before seal")
            try:
                if arm == "M":
                    tail = staged["M_FOOTER_AND_TRAILER"]
                    start = p.operations[fp.ordinal * 2 + 1].range
                    assert start is not None
                    summary = footer.compare_m(
                        tail[:-8],
                        tail[-8:],
                        footer_start=start[0],
                        bindings=fp.bindings,
                        check=self.supervisor.checkpoint,
                    )
                else:
                    trailer = staged["T_TRAILER"]
                    meta = staged["T_FOOTER_FROM_TRAILER"]
                    summary = footer.compare_t(
                        meta,
                        trailer,
                        footer_start=fp.remote_length - 8 - len(meta),
                        bindings=fp.bindings,
                        check=self.supervisor.checkpoint,
                    )
            except footer.FooterError as exc:
                raise _Stop(f"{fp.file}: frozen binding mismatch: {exc}") from exc
            except memory.MemoryGuardError as exc:
                raise _Stop(f"memory supervision abort during parse: {exc}") from exc
            except memory.StepDeadlineExceeded as exc:
                raise _Stop("parse exceeded its step deadline") from exc
            files.append(
                {
                    "file": fp.file,
                    "strong_etag": fp.strong_etag,
                    "remote_length": fp.remote_length,
                    "staged": {
                        c["op"]: {"rel": c["staged"], "sha256": c["body_sha256"]} for c in cells
                    },
                    "facts": {c["op"]: c["facts"] for c in cells},
                    "summary": summary,
                }
            )
        st = self.state.arms[arm]
        result: dict[str, Any] = {
            "kind": RESULT_KIND,
            "schema_version": 1,
            "epoch_id": frozen_v3.EPOCH_ID,
            "mode": self.ctx.mode,
            "arm": arm,
            "plan_digest": p.digest,
            "authorization_digest": self.ctx.auth.digest,
            "epoch_start_digest": self.start_digest,
            "files": files,
            "ledger_at_seal": {
                "requests": st.requests,
                "requests_by_file": dict(sorted(st.requests_by_file.items())),
                "body_bytes": st.body_charged,
                "body_bytes_by_file": dict(sorted(st.body_charged_by_file.items())),
            },
            "network": "STOPPED after this arm's Phase P; Phase D not authorized",
        }
        result["digest"] = canonical.self_digest(result)
        self._write(
            f"phase_p/{arm.lower()}/result.json", canonical.canonical_bytes(result), arms=[arm]
        )
        self._time(arm, step="close", hold_file=None, hold_ns=0)
        self._commit(
            "PHASE",
            {
                "arm": arm,
                "to": "P_COMPLETE_SEALED",
                "reason": None,
                "result_digest": result["digest"],
            },
        )


def _clone(state: journal.EpochState) -> journal.EpochState:
    return copy.deepcopy(state)


def execute_phase_p(
    *,
    repo_root: Path,
    root: Path,
    authorization_path: Path,
    approval_path: Path,
    review_path: Path,
    harness: SyntheticHarness | None = None,
    max_operations: int | None = None,
) -> dict[str, Any]:
    """Run or resume the authorized Phase-P plan (M then T). Phase P only."""
    if max_operations is not None and (type(max_operations) is not int or max_operations < 0):
        raise PhasePError("max_operations must be a non-negative integer")
    ctx = _build_context(
        repo_root=repo_root,
        root=root,
        authorization_path=authorization_path,
        approval_path=approval_path,
        review_path=review_path,
        harness=harness,
    )
    session = _Session(ctx, root)
    try:
        return session.run(max_operations)
    except journal.JournalError as exc:
        raise PhasePError(
            f"journal failure; session stopped, next load conserves open work: {exc}"
        ) from exc
    finally:
        session.close()


def inspect_epoch(root: Path) -> dict[str, Any]:
    """Read-only summary: replays the journal without locking or writing."""
    try:
        identity = fsroot.observe_root(root)
        fs = fsroot.RootFS(identity)
        start = canonical.loads_bytes_strict(
            fs.read_bytes(genesis.EPOCH_START_REL, max_bytes=1_048_576)
        )
        raw = fs.read_bytes(journal.JOURNAL_REL, max_bytes=journal.JOURNAL_ALLOWANCE)
    except (OSError, fsroot.ContainmentError, canonical.CanonicalError) as exc:
        raise PhasePError(f"cannot inspect epoch: {exc}") from exc
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    records = [canonical.loads_bytes_strict(line) for line in lines]
    state = journal.new_state(records[0]["body"], frozen_v3.EPOCH_ID)
    for record in records:
        state.apply(record)
    return {
        "epoch_start_digest": start["digest"],
        "records": len(records),
        "open_session": state.session,
        "sessions": state.sessions,
        "open_attempt": state.open_attempt,
        "arms": {
            arm: {
                "phase": s.phase,
                "requests": s.requests,
                "requests_by_file": dict(s.requests_by_file),
                "body_charged": s.body_charged,
                "body_charged_by_file": dict(s.body_charged_by_file),
                "body_held": s.body_held,
                "time_ns": s.time_ns,
                "time_ns_by_file": dict(s.time_ns_by_file),
                "hold_ns": s.hold_ns,
                "ops_done": len(s.ops_done),
                "result_digest": s.result_digest,
                "incomplete_reason": s.incomplete_reason,
            }
            for arm, s in state.arms.items()
        },
        "disk": {
            rel: {"size": e.size, "category": e.category, "arms": list(e.arms)}
            for rel, e in state.disk.items()
        },
        "pending_disk": sorted(state.pending),
        "attempts": [
            {
                "attempt_id": a.attempt_id,
                "arm": a.arm,
                "file": a.file,
                "op_index": a.op_index,
                "outcome": a.outcome,
                "charged": a.charged,
                "body_total": a.body_total,
                "reservation": a.reservation,
            }
            for a in state.attempts.values()
        ],
        "aborts": list(state.aborts),
    }
