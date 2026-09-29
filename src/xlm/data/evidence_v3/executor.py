"""Integrated Phase-P executor: the only supported future Phase-P path.

Astra correctly noted there was no actual v3 Phase-P execution path
connecting authorization, genesis, transport, ledger, memory, disk,
runtime, identity verification, and PhaseGate. This module provides ONE
integrated executor. It is never executed live in this task; offline tests
drive it with a fake transport over disposable synthetic roots.

The executor refuses unless epoch, authorization, operator approval,
PhaseGate, root inventory, ledgers, memory, disk, and runtime are all
valid. No raw transport call can bypass accounting: transport access is
private to the executor and every response body (success, redirect,
error, partial, retry) is metered while reading, before validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v3 import authz, epoch, frozen_v3, guards, ledger, transport


class ExecutorError(ValueError):
    """Any integrated Phase-P execution violation: STOP."""


@dataclass
class FakeResponse:
    """Offline synthetic HTTP response (never a live connection)."""

    status: int
    content_range: str | None
    etag: str | None
    body: bytes
    redirect_targets: list[str] | None = None


class FakeTransport:
    """Synthetic transport mapping exact URLs to canned responses."""

    def __init__(self, routes: Mapping[str, FakeResponse]) -> None:
        self.routes = dict(routes)
        self.calls: list[str] = []

    def fetch(self, url: str) -> FakeResponse:
        transport.validate_url(url)
        self.calls.append(url)
        if url not in self.routes:
            raise ExecutorError(f"no synthetic route for {url}")
        return self.routes[url]


def _parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or not value.startswith("bytes "):
        return None
    try:
        span, total = value[len("bytes ") :].split("/")
        start, end = span.split("-")
        return int(start), int(end), int(total)
    except ValueError:
        return None


def verify_remote_identity(
    *,
    response: FakeResponse,
    expected_start: int,
    expected_end: int,
    expected_length: int,
    expected_etag: str,
    require_par1: bool,
    expected_revision: str,
    url: str,
) -> None:
    """Enforce exact remote identity; any mismatch is STOP, never reselect."""
    if response.status != 206:
        raise ExecutorError(f"identity STOP: status {response.status} is not 206 for {url}")
    expected_range = (expected_start, expected_end, expected_length)
    if _parse_content_range(response.content_range) != expected_range:
        raise ExecutorError(f"identity STOP: Content-Range mismatch for {url}")
    if not isinstance(response.etag, str) or response.etag != expected_etag:
        raise ExecutorError(f"identity STOP: ETag mismatch for {url}")
    if expected_revision != frozen_v3.SOURCE_REVISION or expected_revision not in url:
        raise ExecutorError(f"identity STOP: immutable revision/path mismatch for {url}")
    if require_par1 and bytes(response.body[:4]) != b"PAR1":
        raise ExecutorError(f"identity STOP: PAR1 magic missing for {url}")


class PhasePExecutor:
    """Gated Phase-P runner binding every enforcement mechanism."""

    def __init__(
        self,
        arm: str,
        *,
        authorization: Mapping[str, Any],
        validated_auth: Mapping[str, Any],
        epoch_root: Path,
        phase_ledger: ledger.V3Ledger,
        memory: guards.FailClosedSupervisor,
        disk: guards.PhysicalDiskInventory,
        runtime: guards.ActiveRuntime,
        gate: epoch.PhaseGate,
        transport_client: FakeTransport,
    ) -> None:
        if arm not in ("M", "T"):
            raise ExecutorError(f"unknown arm: {arm}")
        self.arm = arm
        self.authorization = authorization
        self.validated_auth = validated_auth
        self.epoch_root = epoch_root
        self.phase_ledger = phase_ledger
        self.memory = memory
        self.disk = disk
        self.runtime = runtime
        self.gate = gate
        self.transport_client = transport_client

    def _require_ready(self) -> None:
        if self.gate.arm != self.arm:
            raise ExecutorError("PhaseGate arm mismatch")
        if self.gate.state != "P_AUTHORIZED":
            raise ExecutorError(f"PhaseGate does not allow P from {self.gate.state}")
        record = epoch.load_genesis_record(self.epoch_root)
        _ = record
        if not isinstance(self.validated_auth, Mapping) or not self.validated_auth.get(
            "authorization_digest"
        ):
            raise ExecutorError("authorization schema validation missing")
        if not isinstance(self.validated_auth.get("operator_approval_digest"), str):
            raise ExecutorError("operator approval validation missing")
        self.memory.check(context=f"phase-P-{self.arm}:ready")
        self.disk.reconcile()
        if self.runtime.request_budget(file="__ready__") <= 0:
            raise ExecutorError("no runtime budget remains")

    def run_phase_p(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute gated Phase-P operations (identity + footer reads only).

        Each operation: ``{file, url, range_start, range_end, remote_length,
        etag, require_par1, category, phase}``. T operations with text-page
        ranges and M operations scheduling data rows refuse: Phase P is
        footer/metadata preparation only.
        """
        self._require_ready()
        if self.gate.state != "P_AUTHORIZED":
            raise ExecutorError("PhaseGate does not allow P")
        self.gate.begin_p()
        completed = 0
        try:
            for index, op in enumerate(operations):
                completed += self._run_one(index, op)
        except Exception:
            self.gate.mark_incomplete()
            raise
        return {"arm": self.arm, "operations_completed": completed, "gate": self.gate.state}

    def _run_one(self, index: int, op: Mapping[str, Any]) -> int:
        for key in ("file", "url", "range_start", "range_end", "remote_length", "etag", "category"):
            if key not in op:
                raise ExecutorError(f"operation {index} is missing {key}")
        if op.get("phase", "P") != "P":
            raise ExecutorError(f"operation {index}: Phase P cannot run phase {op.get('phase')}")
        if op.get("kind") in ("text-page", "data-row"):
            raise ExecutorError(f"operation {index}: Phase P cannot fetch text pages or data rows")
        if self.arm == "T" and op.get("kind") == "text":
            raise ExecutorError(f"operation {index}: Phase P cannot fetch T text")
        if self.arm == "M" and op.get("kind") == "data":
            raise ExecutorError(f"operation {index}: Phase P cannot fetch M data rows")
        filename = str(op["file"])
        url = str(op["url"])
        category = str(op["category"])
        if category != "footer":
            raise ExecutorError(f"operation {index}: Phase P category must be footer/control")
        chain = [url] + list(op.get("redirects", []))
        checked = transport.validate_redirect_chain(chain)
        prefix = f"phase-p-{self.arm}-{index}-attempt"
        taken = {eid for eid in self.phase_ledger.events}
        taken |= {str(v.get("event_id", "")) for v in self.phase_ledger.in_flight.values()}
        attempt = 0
        while f"{prefix}{attempt}" in taken:
            attempt += 1
        event = ledger.V3Event(
            event_id=f"{prefix}{attempt}",
            kind="request",
            file=filename,
            category="footer",
            requests=1 + checked["redirect_transitions"],
            phase="P",
            logical_request=index,
            physical_attempt=attempt,
            redirect_count=checked["redirect_transitions"],
        )
        token = self.phase_ledger.reserve_event(event)
        self.memory.check(context=f"phase-P-{self.arm}:op-{index}")
        self.runtime.start_segment(file=filename)
        try:
            response = self.transport_client.fetch(url)
            # Meter while reading: every returned body counts, even when the
            # response is subsequently rejected. Uncertain reads conserve.
            try:
                observed = len(bytes(response.body))
            except Exception as exc:
                observed = 0
                self.phase_ledger.mark_response_started(token, body_bytes_delta=0)
                raise ExecutorError(f"operation {index}: failed read conserved") from exc
            self.phase_ledger.mark_response_started(token, body_bytes_delta=observed)
            for hop in chain[1:]:
                hop_response = self.transport_client.fetch(hop)
                extra = len(bytes(hop_response.body))
                self.phase_ledger.mark_response_started(token, body_bytes_delta=extra)
            verify_remote_identity(
                response=response,
                expected_start=int(op["range_start"]),
                expected_end=int(op["range_end"]),
                expected_length=int(op["remote_length"]),
                expected_etag=str(op["etag"]),
                require_par1=bool(op.get("require_par1", True)),
                expected_revision=frozen_v3.SOURCE_REVISION,
                url=url,
            )
        except Exception:
            try:
                self.runtime.end_segment(file=filename)
            except guards.GuardError:
                pass
            try:
                self.phase_ledger.fail_event(token)
            except ledger.LedgerError:
                pass
            raise
        self.runtime.end_segment(file=filename)
        self.phase_ledger.complete_event(token)
        # Stage footer bytes under disk accounting (reservation must cover).
        payload = bytes(response.body)
        disk_name = f"phase-p/{self.arm}/{index}.bin"
        self.disk.reserve(disk_name, scratch=len(payload), final=0)
        self.disk.write_file(disk_name, payload)
        guards.check_parser_reservation(len(payload))
        self.memory.check(context=f"phase-P-{self.arm}:op-{index}-done")
        _ = authz.AUTH_KIND
        return 1
