"""Shared arm-budget ledgers with exact frozen ceilings (Arms M and T).

One ledger per arm spans ALL stages (footer planning + execution): footer
and execution share a single request counter; retries, redirects, and
failures are charged; resume reuses the same ledger object — there is no
reset API. Every charge refuses past the ceiling instead of saturating.
Disk accounting includes partials, caches, manifests, duplicate copies,
and extraction expansion via explicit reserve/release calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xlm.data.evidence_v2 import frozen


class BudgetRefusal(RuntimeError):
    """Cap exhausted, deadline passed, or unsafe bound: STOP the arm."""


class ArmLedger:
    """Shared mutable budget for one arm; stages share, never reset."""

    def __init__(
        self,
        arm: str,
        ceilings: Mapping[str, int],
        *,
        requests_key: str,
        transfer_arm_key: str,
        decompressed_arm_key: str,
        disk_scratch_key: str,
        disk_final_key: str,
        disk_combined_key: str,
        time_arm_key: str,
        combined_file_requests_key: str | None = None,
        data_file_key: str | None = None,
        data_arm_key: str | None = None,
        total_file_key: str | None = None,
        total_arm_key: str | None = None,
        footer_file_key: str | None = None,
        footer_arm_key: str | None = None,
    ) -> None:
        if arm not in ("M", "T"):
            raise BudgetRefusal(f"unknown arm: {arm}")
        self.arm = arm
        self.ceilings = dict(ceilings)
        self._requests_key = requests_key
        self._transfer_arm_key = transfer_arm_key
        self._decompressed_arm_key = decompressed_arm_key
        self._disk_scratch_key = disk_scratch_key
        self._disk_final_key = disk_final_key
        self._disk_combined_key = disk_combined_key
        self._time_arm_key = time_arm_key
        # Optional combined per-file request total (footer+data share it).
        self._combined_file_requests_key = combined_file_requests_key
        # Optional transfer subcap/total split (no borrowing between stages).
        self._data_file_key = data_file_key
        self._data_arm_key = data_arm_key
        self._total_file_key = total_file_key
        self._total_arm_key = total_arm_key
        self._footer_file_key = footer_file_key
        self._footer_arm_key = footer_arm_key
        self.requests = 0
        self.failed_requests = 0
        self.retries = 0
        self.redirects = 0
        self.response_body_bytes = 0
        self.kind_requests: dict[str, int] = {}
        self.footer_body_bytes = 0
        self.transfer_by_category: dict[str, int] = {"footer": 0, "data": 0}
        self.transfer_per_file: dict[str, dict[str, int]] = {}
        self.decompressed_per_file: dict[str, int] = {}
        self.decompressed_arm = 0
        self.disk_scratch = 0
        self.disk_final = 0
        self.scanned_per_file: dict[str, int] = {}
        self.scanned_arm = 0
        self.elapsed_seconds = 0.0
        self.refusals: list[str] = []
        self._file_requests: dict[str, dict[str, int]] = {}

    # -- internal guards -------------------------------------------------
    def _ceiling(self, key: str) -> int:
        try:
            return self.ceilings[key]
        except KeyError as exc:
            raise BudgetRefusal(f"arm {self.arm} has no ceiling {key}") from exc

    def _refuse(self, message: str) -> BudgetRefusal:
        self.refusals.append(message)
        return BudgetRefusal(f"arm {self.arm}: {message}")

    # -- shared request counter: every attempt, redirect, retry, failure --
    def charge_request(
        self,
        *,
        kind: str,
        retried: bool = False,
        redirected_hops: int = 0,
        failed: bool = False,
    ) -> None:
        """Charge one logical request plus its attempts/hops to the arm."""
        if redirected_hops > self._ceiling("max_redirect_hops"):
            raise self._refuse(f"{kind}: {redirected_hops} redirect hops exceed cap")
        self.requests += 1
        self.kind_requests[kind] = self.kind_requests.get(kind, 0) + 1
        if retried:
            self.retries += 1
        self.redirects += redirected_hops
        if failed:
            self.failed_requests += 1
        if self.requests > self._ceiling(self._requests_key):
            raise self._refuse(f"{kind}: arm request ceiling exhausted")
        # Arm M splits the arm total into frozen footer/data stage totals
        # sharing this same counter: footer planning stops at 80 even
        # though later data execution may continue to 800/880.
        stage_key = {"M": {"footer": "footer_requests_total", "data": "data_requests_total"}}
        key = stage_key.get(self.arm, {}).get(kind)
        if key is not None and self.kind_requests[kind] > self._ceiling(key):
            raise self._refuse(f"{kind}: arm {kind} request ceiling exhausted")

    def note_failed_attempt(self) -> None:
        """Count a failed attempt without consuming another request charge."""
        self.failed_requests += 1

    def note_redirects(self, count: int) -> None:
        """Count followed redirect transitions (requests themselves are charged)."""
        if count < 0:
            raise self._refuse("negative redirect count")
        self.redirects += count

    def charge_file_request(self, source_file: str, *, kind: str) -> None:
        """One attempt charged to the shared arm counter AND the file cell."""
        self.charge_request(kind=kind)
        if self.arm == "M":
            per_file_key = (
                "footer_requests_per_file" if kind == "footer" else "data_requests_per_plan"
            )
        else:
            per_file_key = "requests_per_file_max"
        per_file = self._file_requests.setdefault(source_file, {})
        per_file[kind] = per_file.get(kind, 0) + 1
        if per_file[kind] > self._ceiling(per_file_key):
            raise self._refuse(f"{kind}: per-file request ceiling for {source_file}")
        if self._combined_file_requests_key is not None:
            combined = sum(per_file.values())
            if combined > self._ceiling(self._combined_file_requests_key):
                raise self._refuse(f"{kind}: combined per-file request ceiling for {source_file}")

    # -- transfer / bodies ------------------------------------------------
    def charge_response_body(self, nbytes: int, *, kind: str) -> None:
        """Response bodies and single-range buffers share the body cap."""
        if nbytes > self._ceiling("response_body_bytes_max"):
            raise self._refuse(f"{kind}: single response body {nbytes} exceeds cap")
        self.response_body_bytes += nbytes
        if self.response_body_bytes > self._ceiling(self._transfer_arm_key):
            raise self._refuse(f"{kind}: arm transfer ceiling exhausted")
        # Footer response bodies share the arm transfer counter and stop at
        # the frozen footer byte total (32 MiB M, 16 MiB T).
        if kind == "footer":
            self.footer_body_bytes += nbytes
            footer_key = (
                self._footer_arm_key
                if self._footer_arm_key is not None
                else ("footer_bytes_total" if self.arm == "M" else "footer_bytes_total_max")
            )
            if self.footer_body_bytes > self._ceiling(footer_key):
                raise self._refuse(f"{kind}: arm footer byte ceiling exhausted")

    def _check_category(self, category_totals: Mapping[str, int], kind: str) -> None:
        """Enforce data/footer subcaps plus combined totals when configured."""
        if self._data_arm_key is not None and kind == "data":
            if category_totals["data"] > self._ceiling(self._data_arm_key):
                raise self._refuse(f"{kind}: arm data byte ceiling exhausted")
        if self._footer_arm_key is not None and kind == "footer":
            if category_totals["footer"] > self._ceiling(self._footer_arm_key):
                raise self._refuse(f"{kind}: arm footer byte ceiling exhausted")
        if self._total_arm_key is not None:
            total = category_totals["footer"] + category_totals["data"]
            if total > self._ceiling(self._total_arm_key):
                raise self._refuse(f"{kind}: arm total byte ceiling exhausted")

    def charge_transfer(self, source_file: str, nbytes: int, *, kind: str) -> None:
        """Per-file and arm transfer bounds; footer and data share the arm."""
        if kind not in ("footer", "data"):
            raise self._refuse(f"unknown transfer category: {kind}")
        cell = self.transfer_per_file.setdefault(source_file, {"footer": 0, "data": 0})
        if self._data_file_key is not None:
            # Split subcaps with no borrowing: data and footer each stop at
            # their own file ceiling, and their sum stops at the file total.
            sub_key = self._data_file_key if kind == "data" else self._footer_file_key
            if sub_key is None:
                raise self._refuse("transfer split keys misconfigured")
            if cell[kind] + nbytes > self._ceiling(sub_key):
                raise self._refuse(f"{kind}: per-file {kind} byte ceiling for {source_file}")
            if self._total_file_key is not None and cell["footer"] + cell[
                "data"
            ] + nbytes > self._ceiling(self._total_file_key):
                raise self._refuse(f"{kind}: per-file total byte ceiling for {source_file}")
            cell[kind] += nbytes
            self.transfer_by_category[kind] += nbytes
            self._check_category(self.transfer_by_category, kind)
        else:
            # Legacy single-key behavior: kind-specific file key on the mixed
            # per-file total (v2.0 semantics preserved exactly).
            if self.arm == "M":
                per_file_key = (
                    "footer_bytes_per_file" if kind == "footer" else "data_bytes_per_file"
                )
            else:
                per_file_key = (
                    "footer_bytes_per_file_max" if kind == "footer" else "data_bytes_per_file_max"
                )
            got = cell["footer"] + cell["data"] + nbytes
            if got > self._ceiling(per_file_key):
                raise self._refuse(f"{kind}: per-file transfer ceiling for {source_file}")
            cell[kind] += nbytes
            self.transfer_by_category[kind] += nbytes
        self.charge_response_body(nbytes, kind=kind)

    def adopt_history(
        self,
        *,
        kind: str,
        requests: int,
        response_bytes: int = 0,
        source_file: str | None = None,
        category: str = "footer",
    ) -> None:
        """Record already-consumed history without spending new budget.

        Recording never refuses (history is fact); any resulting overrun
        surfaces in readiness checks and refuses subsequent charges. Only
        explicit non-negative integers are accepted; unknown usage must be
        registered separately, never silently zeroed.
        """
        for name, value in (("requests", requests), ("response_bytes", response_bytes)):
            if type(value) is not int or value < 0:
                raise BudgetRefusal(f"adopted {name} must be a non-negative integer")
        if kind not in ("footer", "data") or category not in ("footer", "data"):
            raise BudgetRefusal("adopted kind/category must be footer or data")
        self.requests += requests
        self.kind_requests[kind] = self.kind_requests.get(kind, 0) + requests
        self.response_body_bytes += response_bytes
        self.transfer_by_category[category] += response_bytes
        if kind == "footer":
            self.footer_body_bytes += response_bytes
        if source_file is not None:
            per_file = self._file_requests.setdefault(source_file, {})
            per_file[kind] = per_file.get(kind, 0) + requests
            cell = self.transfer_per_file.setdefault(source_file, {"footer": 0, "data": 0})
            cell[category] += response_bytes

    # -- decompressed work (decoded/discarded/repeated all count) ---------
    def charge_decompressed(self, source_file: str, nbytes: int, *, kind: str) -> None:
        per_file_key = (
            "decompressed_bytes_per_file" if self.arm == "M" else "decompressed_bytes_per_file_max"
        )
        got = self.decompressed_per_file.get(source_file, 0) + nbytes
        if got > self._ceiling(per_file_key):
            raise self._refuse(f"{kind}: per-file decompressed ceiling for {source_file}")
        self.decompressed_per_file[source_file] = got
        self.decompressed_arm += nbytes
        if self.decompressed_arm > self._ceiling(self._decompressed_arm_key):
            raise self._refuse(f"{kind}: arm decompressed ceiling exhausted")

    # -- scanned rows ------------------------------------------------------
    def charge_scanned(self, source_file: str, nrows: int, *, arm_key: str) -> None:
        per_file_key = "max_scan_rows_per_file" if self.arm == "M" else "scan_rows_per_file_max"
        got = self.scanned_per_file.get(source_file, 0) + nrows
        if got > self._ceiling(per_file_key):
            raise self._refuse(f"per-file scan ceiling for {source_file}")
        self.scanned_per_file[source_file] = got
        self.scanned_arm += nrows
        if self.scanned_arm > self._ceiling(arm_key):
            raise self._refuse("arm scan ceiling exhausted")

    # -- disk: scratch/final/combined at every instant ---------------------
    def reserve_disk(self, *, scratch: int = 0, final: int = 0, kind: str) -> None:
        """Reserve before writing; partials/caches/manifests/copies included."""
        if self.disk_scratch + scratch > self._ceiling(self._disk_scratch_key):
            raise self._refuse(f"{kind}: scratch disk ceiling")
        if self.disk_final + final > self._ceiling(self._disk_final_key):
            raise self._refuse(f"{kind}: final artifact ceiling")
        if self.disk_scratch + scratch + self.disk_final + final > self._ceiling(
            self._disk_combined_key
        ):
            raise self._refuse(f"{kind}: combined storage ceiling")
        self.disk_scratch += scratch
        self.disk_final += final

    def release_disk(self, *, scratch: int = 0, final: int = 0) -> None:
        self.disk_scratch = max(0, self.disk_scratch - scratch)
        self.disk_final = max(0, self.disk_final - final)

    # -- time / memory ------------------------------------------------------
    def charge_time(self, seconds: float, *, kind: str) -> None:
        """Earliest deadline wins; stage ceilings checked by callers."""
        if seconds < 0:
            raise self._refuse(f"{kind}: negative time charge")
        self.elapsed_seconds += seconds
        if self.elapsed_seconds > self._ceiling(self._time_arm_key):
            raise self._refuse(f"{kind}: arm deadline exhausted")

    def check_memory(self, resident_bytes: int, *, kind: str) -> None:
        """Supervised resident-bytes check; decode needs preflight first."""
        if resident_bytes > self.ceilings["memory_resident_bytes"]:
            raise self._refuse(f"{kind}: resident process-tree memory ceiling")

    def preflight_decode(self, upper_bound_bytes: int | None, *, kind: str, cap_bytes: int) -> None:
        """Refuse when no safe upper bound exists or it exceeds the cap."""
        if upper_bound_bytes is None:
            raise self._refuse(f"{kind}: no safe decoder upper bound available")
        if upper_bound_bytes > cap_bytes:
            raise self._refuse(f"{kind}: decoder upper bound exceeds cap")

    # -- snapshots -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "requests": self.requests,
            "kind_requests": dict(sorted(self.kind_requests.items())),
            "failed_requests": self.failed_requests,
            "retries": self.retries,
            "redirects": self.redirects,
            "response_body_bytes": self.response_body_bytes,
            "footer_body_bytes": self.footer_body_bytes,
            "transfer_by_category": dict(self.transfer_by_category),
            "transfer_per_file": {
                name: dict(cell) for name, cell in sorted(self.transfer_per_file.items())
            },
            "requests_per_file": {
                name: dict(per_file) for name, per_file in sorted(self._file_requests.items())
            },
            "decompressed_arm": self.decompressed_arm,
            "decompressed_per_file": dict(sorted(self.decompressed_per_file.items())),
            "scanned_arm": self.scanned_arm,
            "scanned_per_file": dict(sorted(self.scanned_per_file.items())),
            "disk_scratch": self.disk_scratch,
            "disk_final": self.disk_final,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "refusals": list(self.refusals),
        }


def new_arm_m() -> ArmLedger:
    """Shared ledger for Arm M with the exact §4 ceilings."""
    return ArmLedger(
        "M",
        frozen.ARM_M_LIMITS,
        requests_key="requests_total",
        transfer_arm_key="response_body_bytes_total",
        decompressed_arm_key="decompressed_bytes_arm",
        disk_scratch_key="disk_scratch_bytes",
        disk_final_key="disk_final_bytes",
        disk_combined_key="disk_combined_bytes",
        time_arm_key="time_seconds_arm",
    )


def new_arm_t() -> ArmLedger:
    """Shared ledger for Arm T with the exact §7 ceilings."""
    return ArmLedger(
        "T",
        frozen.ARM_T_LIMITS,
        requests_key="requests_arm_max",
        transfer_arm_key="transfer_bytes_arm_max",
        decompressed_arm_key="decompressed_bytes_arm_max",
        disk_scratch_key="disk_scratch_bytes",
        disk_final_key="disk_final_bytes",
        disk_combined_key="disk_combined_bytes",
        time_arm_key="time_seconds_arm",
        combined_file_requests_key="requests_per_file_max",
    )


def new_arm_t_v21() -> ArmLedger:
    """Shared ledger for Arm T under the v2.1 transfer amendment.

    Only the transfer ceilings change (frozen v2.1 values); every other
    limit, the request/decompression/scan/disk/time rules, and the
    scientific namespace stay exactly as in v2.0.
    """
    return ArmLedger(
        "T",
        frozen.V21_T_LIMITS,
        requests_key="requests_arm_max",
        transfer_arm_key="transfer_bytes_arm_max",
        decompressed_arm_key="decompressed_bytes_arm_max",
        disk_scratch_key="disk_scratch_bytes",
        disk_final_key="disk_final_bytes",
        disk_combined_key="disk_combined_bytes",
        time_arm_key="time_seconds_arm",
        combined_file_requests_key="requests_per_file_max",
        data_file_key="data_bytes_per_file_max",
        data_arm_key="data_bytes_arm_max",
        total_file_key="transfer_bytes_per_file_max",
        total_arm_key="transfer_bytes_arm_max",
        footer_file_key="footer_bytes_per_file_max",
        footer_arm_key="footer_bytes_total_max",
    )
