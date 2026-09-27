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
        self.requests = 0
        self.failed_requests = 0
        self.retries = 0
        self.redirects = 0
        self.response_body_bytes = 0
        self.transfer_per_file: dict[str, int] = {}
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
        if retried:
            self.retries += 1
        self.redirects += redirected_hops
        if failed:
            self.failed_requests += 1
        if self.requests > self._ceiling(self._requests_key):
            raise self._refuse(f"{kind}: arm request ceiling exhausted")

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

    # -- transfer / bodies ------------------------------------------------
    def charge_response_body(self, nbytes: int, *, kind: str) -> None:
        """Response bodies and single-range buffers share the body cap."""
        if nbytes > self._ceiling("response_body_bytes_max"):
            raise self._refuse(f"{kind}: single response body {nbytes} exceeds cap")
        self.response_body_bytes += nbytes
        if self.response_body_bytes > self._ceiling(self._transfer_arm_key):
            raise self._refuse(f"{kind}: arm transfer ceiling exhausted")

    def charge_transfer(self, source_file: str, nbytes: int, *, kind: str) -> None:
        """Per-file and arm transfer bounds; footer and data share the arm."""
        per_file_key = (
            "footer_bytes_per_file"
            if kind == "footer"
            else "data_bytes_per_file"
            if self.arm == "M"
            else "transfer_bytes_per_file_max"
        )
        got = self.transfer_per_file.get(source_file, 0) + nbytes
        if got > self._ceiling(per_file_key):
            raise self._refuse(f"{kind}: per-file transfer ceiling for {source_file}")
        self.transfer_per_file[source_file] = got
        self.charge_response_body(nbytes, kind=kind)

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
            "failed_requests": self.failed_requests,
            "retries": self.retries,
            "redirects": self.redirects,
            "response_body_bytes": self.response_body_bytes,
            "transfer_per_file": dict(sorted(self.transfer_per_file.items())),
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
    )
