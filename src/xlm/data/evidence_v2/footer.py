"""Live footer/cost planning transport for Arm M (protocol section 4).

Composes the existing audited XLM machinery — ``canonical_range_url``,
``validate_host``, ``TransportBudget``, ``SafeRedirectHandler``,
``discover_layout_over_ranges``, ``plan_sample_windows`` — with the
frozen ArmLedger holding every counter and the frozen window-v2
identities cross-checked. Footer ranges only: file identity/length,
Parquet footer/schema, row-group and projected-chunk metadata, physical
cost estimation, deterministic window planning. Never reads row
payloads, never decodes corpus rows, never touches the text column.

The agent's own verification uses the fake transport below with authored
Parquet fixtures; live transport runs only under the operator's
separately authorized ``plan-footers --no-dry-run --authorize-network``.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xlm.data.acquisition.fetcher import CONTENT_RANGE_RE
from xlm.data.acquisition.plan import ParquetWindowDecode
from xlm.data.acquisition.projection import resolve_projection
from xlm.data.acquisition.sampling import (
    SamplingRefusal,
    SamplingRequest,
    _window_estimates,
    _window_group_refusal,
    canonical_range_url,
    discover_layout_over_ranges,
    plan_sample_windows,
)
from xlm.data.evidence_v2 import budgets, canonical, frozen, windows
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    DeadlineExceededError,
    HostNotAllowlistedError,
    SafeRedirectHandler,
    TransportBudget,
    validate_host,
)


class FooterError(ValueError):
    """Any transport, identity, schema, budget, or feasibility refusal.

    Carries bounded diagnostics: ``attempt_redirects`` (Location
    transitions followed while resolving the failed issued request) and
    ``redirect_chain`` (per-hop status/host records, credentials redacted).
    """

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics: dict[str, Any] = dict(diagnostics) if diagnostics else {}
        self.attempt_redirects: int = int(self.diagnostics.get("transitions", 0))


RETRYABLE_HTTP = frozenset(frozen.RETRYABLE_HTTP)
RETRY_DELAYS = (1.0, 2.0)


def _sanitize_url(url: str) -> dict[str, str]:
    """Host + path only: never userinfo, query (signed credentials), or fragment."""
    parsed = urllib.parse.urlparse(url)
    return {
        "host": (parsed.hostname or "").lower(),
        "scheme": parsed.scheme,
        "path": parsed.path,
    }


@dataclass
class RangeEvidence:
    """One bounded range response: body, total length, ETag if served."""

    body: bytes
    total_length: int
    etag: str | None = None
    redirect_hops: int = 0
    redirect_chain: tuple[dict[str, Any], ...] = ()


class HopCountingRedirectHandler(SafeRedirectHandler):
    """Allowlisted redirects with the frozen <=3 hop ceiling enforced.

    Counting semantics (protocol: at most THREE actual Location transitions
    after the original request): ``hops`` counts transitions followed while
    resolving ONE issued request (one ``opener.open`` call). The original
    request is hop 0 and is never counted. The counter resets for every
    issued request — including retries, which re-resolve independently —
    via :meth:`reset_chain`. It is never arm-scoped: chains reset per
    logical request, not per whole arm. Request *budget* still charges
    every attempt plus every redirect through the transport budget.
    """

    def __init__(self, budget: TransportBudget | None = None) -> None:
        super().__init__(budget)
        self.hops = 0
        self.chain: list[dict[str, Any]] = []

    def reset_chain(self) -> None:
        """Start a new resolution chain for the next issued request."""
        self.hops = 0
        self.chain = []

    def _record(
        self,
        *,
        code: int,
        source_url: str,
        dest_url: str,
        allowlist_ok: bool,
        refused: str | None = None,
    ) -> None:
        source = _sanitize_url(source_url)
        dest = _sanitize_url(dest_url)
        self.chain.append(
            {
                "hop": self.hops,
                "status": code,
                "source_host": source["host"],
                "dest_host": dest["host"],
                "dest_path": dest["path"],
                "allowlist_ok": allowlist_ok,
                "refused": refused,
            }
        )

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        source_url = req.get_full_url() if hasattr(req, "get_full_url") else ""
        try:
            validate_host(newurl)
        except HostNotAllowlistedError:
            self._record(
                code=code,
                source_url=source_url,
                dest_url=newurl,
                allowlist_ok=False,
                refused="destination host failed allowlist validation",
            )
            raise
        self.hops += 1
        if self.hops > frozen.MAX_REDIRECT_HOPS:
            self._record(
                code=code,
                source_url=source_url,
                dest_url=newurl,
                allowlist_ok=True,
                refused=f"redirect hop {self.hops} exceeds the 3-hop ceiling",
            )
            raise FooterError(
                f"redirect hop {self.hops} exceeds the 3-hop ceiling",
                diagnostics={
                    "transitions": self.hops,
                    "redirect_chain": list(self.chain),
                },
            )
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        self._record(code=code, source_url=source_url, dest_url=newurl, allowlist_ok=True)
        return result


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionError, OSError)):
        return True
    return False


class LiveFooterTransport:
    """Footer-only range transport over the existing urllib budget stack."""

    def __init__(
        self,
        budget: TransportBudget,
        *,
        timeout_seconds: float = float(frozen.PER_REQUEST_TIMEOUT_SECONDS),
        sleep: Callable[[float], None] = time.sleep,
        opener_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        handler = HopCountingRedirectHandler(budget)
        self.handler = handler
        self.opener = (
            opener_factory(handler)
            if opener_factory is not None
            else urllib.request.build_opener(handler)
        )

    def canonical_url(self, source_file: str) -> str:
        """Frozen resource URL; revision pinned, never caller-supplied."""
        url = canonical_range_url("huggingface", frozen.REPOSITORY, frozen.REVISION, source_file)
        validate_host(url)
        return url

    def _attempt_diagnostics(self, attempt: int) -> dict[str, Any]:
        """Bounded per-attempt redirect evidence for failure receipts."""
        return {
            "attempt": attempt,
            "transitions": self.handler.hops,
            "redirect_chain": list(self.handler.chain),
        }

    @staticmethod
    def _with_diagnostics(exc: FooterError, diagnostics: dict[str, Any]) -> FooterError:
        for key, value in diagnostics.items():
            exc.diagnostics.setdefault(key, value)
        exc.attempt_redirects = int(exc.diagnostics.get("transitions", 0))
        return exc

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        """One footer range with protocol retry semantics; attempts charged.

        The redirect chain resets for every issued request (every retry
        attempt re-resolves independently): the 3-hop ceiling bounds one
        resolution chain, never the arm. Retries add attempts, never hops.
        """
        if not 0 <= start <= end:
            raise FooterError(f"invalid footer range [{start}, {end}]")
        if end - start + 1 > frozen.ARM_M_LIMITS["response_body_bytes_max"]:
            raise FooterError(f"footer range [{start}, {end}] exceeds the 4 MiB body cap")
        url = self.canonical_url(source_file)
        last: BaseException | None = None
        for attempt in range(frozen.MAX_RETRIES + 1):
            self.handler.reset_chain()
            self.budget.record_request()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "xlm-acquisition/2",
                    "Accept-Encoding": "identity",
                    "Range": f"bytes={start}-{end}",
                },
            )
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    evidence = self._read_response(response, source_file, start, end)
                    evidence.redirect_hops = self.handler.hops
                    evidence.redirect_chain = tuple(self.handler.chain)
                    return evidence
            except FooterError as exc:
                self._with_diagnostics(exc, self._attempt_diagnostics(attempt))
                raise
            except (BudgetExhaustedError, DeadlineExceededError, HostNotAllowlistedError) as exc:
                raise self._with_diagnostics(
                    FooterError(f"footer range for {source_file} refused: {exc}"),
                    self._attempt_diagnostics(attempt),
                ) from exc
            except BaseException as exc:  # noqa: BLE001 - classified below
                last = exc
                if attempt >= frozen.MAX_RETRIES or not _retryable(exc):
                    break
                self.sleep(RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else 2.0)
        raise self._with_diagnostics(
            FooterError(
                f"footer range for {source_file} failed after {attempt + 1} "
                f"attempt(s): {type(last).__name__ if last else 'unknown'}"
            ),
            self._attempt_diagnostics(attempt),
        )

    def _read_response(
        self, response: Any, source_file: str, start: int, end: int
    ) -> RangeEvidence:
        headers = response.headers or {}
        match = CONTENT_RANGE_RE.fullmatch(headers.get("Content-Range", ""))
        status = getattr(response, "status", None)
        if status != 206 or not match or (int(match[1]), int(match[2])) != (start, end):
            raise FooterError(f"footer range for {source_file} refused by server (status {status})")
        body = self.budget.read_body(response, end - start + 1)
        if len(body) != end - start + 1:
            raise FooterError(f"short footer body for {source_file}")
        etag = headers.get("ETag")
        final = getattr(response, "geturl", lambda: None)()
        if final is not None and final != self.canonical_url(source_file):
            validate_host(final)
        return RangeEvidence(body=body, total_length=int(match[3]), etag=etag)


@dataclass
class FakeImage:
    """Authored in-memory file image for offline transport tests."""

    content: bytes
    etag: str | None = "test-etag"
    drift_total_on_call: int = 0
    drift_etag_on_call: int = 0
    fail_on_calls: dict[int, BaseException] = field(default_factory=dict)
    evil_redirect_on_call: int = 0
    redirect_hops: int = 0


class FakeFooterTransport:
    """Scripted range server: records every range, never decodes anything."""

    def __init__(self, images: Mapping[str, FakeImage]) -> None:
        self.images = dict(images)
        self.calls: list[tuple[str, int, int]] = []
        self.per_file_calls: dict[str, int] = {}

    def canonical_url(self, source_file: str) -> str:
        return f"https://huggingface.co/datasets/{frozen.REPOSITORY}/resolve/{frozen.REVISION}/{source_file}"

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        self.calls.append((source_file, start, end))
        self.per_file_calls[source_file] = self.per_file_calls.get(source_file, 0) + 1
        call = self.per_file_calls[source_file]
        image = self.images.get(source_file)
        if image is None:
            raise FooterError(f"fake: {source_file} is missing")
        if call == image.evil_redirect_on_call:
            raise FooterError(
                "fake: redirect target 'https://evil.example/file' refused by allowlist"
            )
        if call in image.fail_on_calls:
            raise image.fail_on_calls[call]
        total = len(image.content)
        if call == image.drift_total_on_call:
            total += 1
        etag = image.etag
        if call == image.drift_etag_on_call:
            etag = "changed-etag"
        return RangeEvidence(
            body=image.content[start : end + 1],
            total_length=total,
            etag=etag,
            redirect_hops=image.redirect_hops,
            redirect_chain=(),
        )


def _charging_closure(
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
    source_file: str,
    state: dict[str, Any],
    *,
    durable: Any = None,
) -> Any:
    """RangeFetch for footer discovery: charges the shared ArmLedger.

    The attempt itself plus every redirect transition it follows are each
    one footer request in the arm budget ("all redirect requests count").
    Per-range hop counts accumulate boundedly in ``state["ranges"]``.
    When ``durable`` (a DurableLedger) is supplied, each attempt opens an
    in-flight token closed on success, so a crash preserves attempted
    work instead of refunding it.
    """

    def _charge_redirects(count: int) -> None:
        for _ in range(count):
            ledger.charge_file_request(source_file, kind="footer")
        if count:
            ledger.note_redirects(count)

    def _transport_delta() -> int | None:
        budget = getattr(transport, "budget", None)
        transferred = getattr(budget, "bytes_transferred", None)
        return int(transferred) if type(transferred) is int else None

    def _charge_unseen_body(before: int | None, accounted: int) -> None:
        """Charge redirect/error bodies the transport metered but the range
        accounting did not cover (the range body itself is charged
        separately). Fake transports expose no meter and are skipped."""
        after = _transport_delta()
        if before is None or after is None:
            return
        extra = after - before - accounted
        if extra > 0:
            ledger.charge_transfer(source_file, extra, kind="footer")

    def _account_safely(action: Any) -> None:
        """Failure-path accounting must not mask the original error: a cap
        refusal here is recorded in the ledger and the caller re-raises."""
        try:
            action()
        except budgets.BudgetRefusal:
            pass

    def fetch(start: int, end: int) -> tuple[bytes, int]:
        ledger.charge_file_request(source_file, kind="footer")
        before = _transport_delta()
        token = durable.begin_range(source_file, start, end) if durable is not None else None
        try:
            evidence = transport.fetch_range(source_file, start, end)
        except FooterError as exc:
            ledger.note_failed_attempt()
            _account_safely(
                lambda exc=exc: _charge_redirects(int(getattr(exc, "attempt_redirects", 0) or 0))
            )
            _account_safely(lambda: _charge_unseen_body(before, 0))
            exc.diagnostics.setdefault("file", source_file)
            exc.diagnostics.setdefault("range", [start, end])
            exc.diagnostics.setdefault("resource", transport.canonical_url(source_file))
            raise
        except Exception:
            ledger.note_failed_attempt()
            _account_safely(lambda: _charge_unseen_body(before, 0))
            raise
        _charge_redirects(evidence.redirect_hops)
        ledger.charge_transfer(source_file, len(evidence.body), kind="footer")
        _charge_unseen_body(before, len(evidence.body))
        if durable is not None and token is not None:
            durable.complete_range(token)
        state.setdefault("ranges", []).append(
            {
                "start": start,
                "end": end,
                "bytes": len(evidence.body),
                "hops": evidence.redirect_hops,
            }
        )
        if state.get("total") is None:
            state["total"] = evidence.total_length
            state["etag"] = evidence.etag
        else:
            if evidence.total_length != state["total"]:
                raise FooterError(f"remote length drift for {source_file}: STOP")
            if state.get("etag") is not None and evidence.etag != state["etag"]:
                raise FooterError(f"ETag drift for {source_file}: STOP")
        return evidence.body, evidence.total_length

    return fetch


def _reraise_discovery(source_file: str, exc: SamplingRefusal) -> BaseException:
    """Preserve redirect/budget diagnostics through the layout wrapper.

    ``discover_layout_over_ranges`` wraps non-refusal failures in
    ``SamplingRefusal``; unwrap the cause chain so hop chains and budget
    refusals reach the failure receipt with their evidence intact.
    """
    cause: BaseException | None = exc.__cause__
    while cause is not None:
        if isinstance(cause, budgets.BudgetRefusal):
            return cause
        if isinstance(cause, FooterError) and cause.diagnostics:
            rebuilt = FooterError(
                f"footer discovery refused for {source_file}: {exc}",
                diagnostics=dict(cause.diagnostics),
            )
            rebuilt.diagnostics.setdefault("file", source_file)
            return rebuilt
        cause = cause.__cause__
    return FooterError(f"footer discovery refused for {source_file}: {exc}")


def frozen_request(source_file: str) -> SamplingRequest:
    """Pinned single-file window-v2 sampling request for live planning."""
    return SamplingRequest(
        source_id=frozen.WINDOW_SOURCE_ID,
        view_id=frozen.WINDOW_VIEW_ID,
        revision=frozen.REVISION,
        files=(source_file,),
        seed=frozen.METADATA_SEED,
        mode="window",
        block_records=frozen.WINDOW_ROWS_PER_FILE,
        target_records=frozen.WINDOW_ROWS_PER_FILE,
        max_parser_bytes=frozen.ARM_M_LIMITS["parser_bytes_max"],
        max_decompression_ratio=15.0,
        projected_fields=tuple(frozen.PROJECTION_METADATA),
        window=ParquetWindowDecode(
            policy_version=2,
            stream_buffer_bytes=frozen.WINDOW_BUFFER_BYTES,
            max_window_scan_rows=frozen.MAX_SCAN_ROWS_PER_FILE,
            batch_rows=frozen.WINDOW_BATCH_ROWS,
            # Frozen §4 v2 small-chunk/aggregate exemption: 16777216 bytes.
            ratio_exempt_bytes=16777216,
        ),
    )


def _group_table(layout: Any, request: SamplingRequest) -> tuple[list[dict[str, Any]], list[int]]:
    """Per-candidate-group physical/eligibility rows plus eligible indices."""
    if request.window is None:
        raise FooterError("window policy is required for group eligibility")
    projected = tuple(request.projected_fields or ())
    rows: list[dict[str, Any]] = []
    eligible: list[int] = []
    for group in layout.groups:
        refusal, selected, _ = _window_group_refusal(group, projected, request, layout)
        if refusal is None:
            worst_scan = group.num_rows
            scan_c, scan_u, transfer, requests = _window_estimates(
                selected, group.num_rows, worst_scan, request.window.stream_buffer_bytes
            )
            rows.append(
                {
                    "group_index": group.index,
                    "rows": group.num_rows,
                    "projected_compressed_bytes": sum(c.compressed for c in selected),
                    "projected_uncompressed_bytes": sum(c.uncompressed for c in selected),
                    "chunks": [
                        {
                            "path": c.path,
                            "compressed_bytes": c.compressed,
                            "uncompressed_bytes": c.uncompressed,
                        }
                        for c in selected
                    ],
                    "worst_scan_rows": worst_scan,
                    "worst_transfer_upper_bytes": transfer,
                    "worst_requests": requests,
                    "eligible": True,
                    "refusal": None,
                }
            )
            eligible.append(group.index)
        else:
            rows.append(
                {
                    "group_index": group.index,
                    "rows": group.num_rows,
                    "projected_compressed_bytes": None,
                    "projected_uncompressed_bytes": None,
                    "chunks": [],
                    "worst_scan_rows": None,
                    "worst_transfer_upper_bytes": None,
                    "worst_requests": None,
                    "eligible": False,
                    "refusal": refusal,
                }
            )
    return rows, eligible


def plan_one_file(
    source_file: str,
    *,
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
    winners: Sequence[str],
) -> dict[str, Any]:
    """Footer-plan one frozen file: layout, eligibility, window, feasibility."""
    if source_file not in winners:
        raise FooterError(f"{source_file} is not one of the eight frozen files: refused")
    if len(winners) != 8:
        raise FooterError("Arm M requires exactly the eight frozen files")
    before = ledger.snapshot()
    state: dict[str, Any] = {}
    try:
        layout = discover_layout_over_ranges(
            source_file,
            _charging_closure(transport, ledger, source_file, state),
            max_parser_bytes=frozen.ARM_M_LIMITS["parser_bytes_max"],
            max_decompression_ratio=15.0,
        )
    except SamplingRefusal as exc:
        raise _reraise_discovery(source_file, exc) from exc
    if layout.schema_refusal is not None:
        raise FooterError(f"schema refused for {source_file}: {layout.schema_refusal}")
    request = frozen_request(source_file)
    try:
        resolved = resolve_projection(layout.field_leaves, list(frozen.PROJECTION_METADATA))
    except Exception as exc:
        raise FooterError(f"projection unresolvable for {source_file}: {exc}") from exc
    leaf_paths = [layout.leaf_paths[i] for i in resolved.leaf_indices]
    if any(p == "text" or p.startswith("text.") for p in leaf_paths):
        raise FooterError(f"text column resolved in Arm-M projection for {source_file}")
    groups, eligible = _group_table(layout, request)
    if not eligible:
        raise FooterError(f"no eligible 512-row group in {source_file}: arm INCOMPLETE")
    try:
        result = plan_sample_windows({source_file: layout}, request)
    except SamplingRefusal as exc:
        raise FooterError(f"window planning refused for {source_file}: {exc}") from exc
    if len(result.windows) != 1:
        raise FooterError(f"expected one window for {source_file}")
    chosen = result.windows[0]
    if chosen.num_rows != frozen.WINDOW_ROWS_PER_FILE:
        raise FooterError(
            f"window holds {chosen.num_rows} rows, not 512, for {source_file}: no rerank"
        )
    # Independent cross-check of the frozen identities over eligible indices.
    eligible_groups = [g for g in layout.groups if g.index in eligible]
    check = windows.freeze_window(
        source_file,
        [g.index for g in eligible_groups],
        [g.num_rows for g in eligible_groups],
        [g.start_row for g in eligible_groups],
    )
    if check["group_index"] != chosen.row_group or check["start_in_group"] != chosen.start_in_group:
        raise FooterError(f"window identity mismatch for {source_file}: STOP")
    after = ledger.snapshot()
    feasibility = _future_feasibility(chosen)
    unit: dict[str, Any] = {
        "file": source_file,
        "remote_length": state.get("total"),
        "etag": state.get("etag"),
        "schema_identity": canonical.digest(
            {"leaf_paths": list(layout.leaf_paths), "refusal": layout.schema_refusal}
        ),
        "projected_logical_fields": list(frozen.PROJECTION_METADATA),
        "projected_physical_leaves": leaf_paths,
        "row_group_count": len(layout.groups),
        "row_groups": groups,
        "selected_group": chosen.row_group,
        "group_start": chosen.group_start_row,
        "relative_window_start": chosen.start_in_group,
        "absolute_window": [chosen.start_row, chosen.stop_row],
        "retained_rows": chosen.num_rows,
        "expected_scan_rows": chosen.expected_scan_rows,
        "projected_transfer_estimate_bytes": chosen.estimated_transfer_upper_bytes,
        "request_estimate": chosen.estimated_requests,
        "window_report": chosen.to_report(),
        "future_plan_feasible": feasibility["feasible"],
        "future_plan_feasibility": feasibility,
        "footer_requests_used": after["requests"] - before["requests"],
        "footer_bytes_used": after["response_body_bytes"] - before["response_body_bytes"],
        "footer_ranges": list(state.get("ranges", [])),
    }
    if not feasibility["feasible"]:
        raise FooterError(f"future data plan cannot fit {source_file}: {feasibility['reasons']}")
    return unit


def _future_feasibility(chosen: Any) -> dict[str, Any]:
    """Whether the FUTURE per-file data plan fits the frozen data caps."""
    reasons: list[str] = []
    requests = int(chosen.domain_estimated_requests or chosen.estimated_requests)
    if requests > frozen.PILOT_REQUEST_CEILING:
        reasons.append(f"request estimate {requests} exceeds 100/plan")
    transfer = int(
        chosen.domain_estimated_transfer_upper_bytes or chosen.estimated_transfer_upper_bytes
    )
    if transfer > frozen.ARM_M_LIMITS["data_bytes_per_file"]:
        reasons.append(f"transfer estimate {transfer} exceeds 28 MiB/file")
    decompressed = int(
        chosen.domain_estimated_scan_uncompressed_bytes or chosen.estimated_scan_uncompressed_bytes
    )
    if decompressed > 67108864:
        reasons.append(f"decompressed estimate {decompressed} exceeds 64 MiB/file")
    scan = int(chosen.domain_scan_rows or chosen.expected_scan_rows)
    if scan > frozen.MAX_SCAN_ROWS_PER_FILE:
        reasons.append(f"scan estimate {scan} exceeds 16384 rows/file")
    reservation = decompressed + frozen.ARM_M_LIMITS["parser_bytes_max"]
    if reservation > 268435456:
        reasons.append(f"process-tree reservation {reservation} exceeds 256 MiB")
    if decompressed <= 0:
        reasons.append("no safe decompressed bound established")
    return {
        "feasible": not reasons,
        "reasons": reasons,
        "request_estimate": requests,
        "transfer_upper_bytes": transfer,
        "decompressed_upper_bytes": decompressed,
        "scan_rows": scan,
        "reservation_bytes": reservation,
    }


class ArmIncomplete(FooterError):
    """Arm M stopped: completed units, failed file, reason, and budget."""

    def __init__(
        self,
        units: list[dict[str, Any]],
        failed_file: str,
        reason: str,
        budget: dict[str, Any],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Arm M INCOMPLETE at {failed_file}: {reason}")
        self.units = units
        self.failed_file = failed_file
        self.reason = reason
        self.budget = budget
        self.diagnostics: dict[str, Any] = dict(diagnostics) if diagnostics else {}


def incomplete_receipt(
    error: ArmIncomplete, *, command: str, exit_status: int = 1
) -> dict[str, Any]:
    """Bounded failure receipt: completed work is descriptive, never success.

    When the failure carries redirect diagnostics (logical request id,
    frozen resource identity, per-hop status/hosts, hop totals, retry
    attempt), they are embedded credential-free for chain reconstruction.
    """
    receipt: dict[str, Any] = {
        "kind": "essential_web_evidence_v2_footer_evidence_incomplete",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "repository": frozen.REPOSITORY,
        "revision": frozen.REVISION,
        "completed_units": error.units,
        "failed_file": error.failed_file,
        "reason": error.reason,
        "redirect_diagnostics": error.diagnostics,
        "budget": error.budget,
        "command": command,
        "exit_status": exit_status,
        "status": "INCOMPLETE",
    }
    return receipt


def plan_arm_m(
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
    winners: Sequence[str],
) -> dict[str, Any]:
    """Footer-plan all eight frozen files in order; first failure stops all."""
    if list(winners) != [
        frozen.crawl_path(c, s[3]) for c, s in zip(frozen.CRAWL_ORDER, frozen.STRATA, strict=True)
    ]:
        raise FooterError("file list is not exactly the eight frozen files in order")
    units: list[dict[str, Any]] = []
    for source_file in winners:
        try:
            units.append(
                plan_one_file(source_file, transport=transport, ledger=ledger, winners=winners)
            )
        except (FooterError, budgets.BudgetRefusal) as exc:
            raise ArmIncomplete(
                units,
                source_file,
                str(exc),
                ledger.snapshot(),
                diagnostics=dict(getattr(exc, "diagnostics", {})),
            ) from exc
    return {
        "kind": "essential_web_evidence_v2_footer_evidence",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "repository": frozen.REPOSITORY,
        "revision": frozen.REVISION,
        "projection": list(frozen.PROJECTION_METADATA),
        "seed": frozen.METADATA_SEED,
        "units": units,
        "budget": ledger.snapshot(),
        "status": "COMPLETE",
    }
