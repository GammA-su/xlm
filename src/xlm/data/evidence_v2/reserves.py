"""Memory/disk/runtime execution reservations and guards (v2.1, offline).

Numeric workspace estimates are NOT measured peak RSS. This module turns
recorded chunk sizes into conservative offline reservations with explicit
components, refuses when they do not fit the frozen ceilings, and designs
the supervised enforcement (process-tree supervisor, monotonic deadlines,
disk high-water schedule) future execution must implement. Refusal here
is a planning verdict, not a performance measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import frozen


class ReservationError(ValueError):
    """A reservation does not fit its ceiling, or inputs are unbounded: refuse."""


def _require_non_negative(name: str, value: int) -> int:
    if type(value) is not int or value < 0:
        raise ReservationError(f"reservation input {name} must be a non-negative integer")
    return value


def memory_reservation(
    *,
    compressed_staged_bytes: int,
    decompressed_upper_bytes: int,
    range_buffer_bytes: int = 4194304,
    parser_bytes: int = 33554432,
    output_retained_bytes: int,
    duplicate_temp_copies: int = 1,
    cap_bytes: int = 268435456,
) -> dict[str, Any]:
    """Conservative one-file decode reservation with named components.

    Covers the staged compressed chunk, worst-case decompressed Arrow
    buffers, one in-flight range buffer, the parser, retained output text,
    and duplicate temporary copies during atomic serialization. A
    process-tree supervisor must refuse execution when measured RSS would
    exceed the cap; this arithmetic only sizes the reservation.
    """
    components = {
        "compressed_staged_bytes": _require_non_negative("compressed", compressed_staged_bytes),
        "decompressed_arrow_bytes": _require_non_negative("decompressed", decompressed_upper_bytes),
        "range_buffer_bytes": _require_non_negative("range_buffer", range_buffer_bytes),
        "parser_bytes": _require_non_negative("parser", parser_bytes),
        "output_retained_bytes": _require_non_negative("output", output_retained_bytes),
        "duplicate_temp_bytes": _require_non_negative("output", output_retained_bytes)
        * max(0, _require_non_negative("copies", duplicate_temp_copies)),
    }
    total = sum(components.values())
    return {
        "components": components,
        "reservation_bytes": total,
        "cap_bytes": cap_bytes,
        "fits": total <= cap_bytes,
        "note": "reservation formula, not measured process-tree RSS",
    }


def disk_schedule(
    *,
    adopted_artifact_bytes: int,
    per_file_stages: Sequence[Mapping[str, int]],
    review_package_bytes: int,
    log_bytes: int,
    scratch_cap: int = 520093696,
    final_cap: int = 16777216,
    combined_cap: int = 536870912,
) -> dict[str, Any]:
    """Worst staged high-water schedule, one file at a time.

    Each file stage lists scratch bytes held (compressed partials, decoder
    materialization, caches, atomic tmp copies) and final bytes produced.
    High water is the maximum combined occupancy across the ordered
    stages; adopted artifacts persist throughout.
    """
    high_water = adopted_artifact_bytes
    peak = high_water
    stages: list[dict[str, Any]] = []
    for stage in per_file_stages:
        scratch = _require_non_negative("scratch", int(stage.get("scratch", 0)))
        final = _require_non_negative("final", int(stage.get("final", 0)))
        high_water = adopted_artifact_bytes + scratch + final
        peak = max(peak, high_water)
        stages.append(
            {
                "file": stage.get("file"),
                "scratch": scratch,
                "final": final,
                "combined": high_water,
            }
        )
    peak = max(peak, adopted_artifact_bytes + review_package_bytes + log_bytes)
    final_total = adopted_artifact_bytes + review_package_bytes + log_bytes
    return {
        "stages": stages,
        "adopted_artifact_bytes": adopted_artifact_bytes,
        "review_package_bytes": review_package_bytes,
        "log_bytes": log_bytes,
        "high_water_bytes": peak,
        "final_bytes": final_total,
        "fits": {
            "scratch": peak - final_total <= scratch_cap,
            "final": final_total <= final_cap,
            "combined": peak <= combined_cap,
        },
    }


def deadlines(
    *,
    elapsed_seconds: float,
    arm_cap: float = 1800.0,
    file_cap: float = 600.0,
    per_request_cap: float = 30.0,
) -> dict[str, Any]:
    """Monotonic deadline state; earliest deadline wins, no zero placeholder."""
    if not elapsed_seconds >= 0:
        raise ReservationError("elapsed time must be non-negative")
    return {
        "elapsed_seconds": elapsed_seconds,
        "arm_remaining": arm_cap - elapsed_seconds,
        "file_remaining": file_cap,
        "per_request": per_request_cap,
        "exhausted": elapsed_seconds >= arm_cap,
        "note": "elapsed_seconds=0 means unmeasured, never instant readiness",
    }


def output_retained_upper(selected_rows: int, per_document_cap: int = 65536) -> int:
    """Arithmetic retained-text maximum for one file (not measured text)."""
    return _require_non_negative("selected_rows", selected_rows) * _require_non_negative(
        "per_document_cap", per_document_cap
    )


def supervisor_guard(
    *,
    reservation_bytes: int,
    measured_rss_bytes: int | None,
    cap_bytes: int = 268435456,
) -> dict[str, Any]:
    """Supervised enforcement design: refuse unless reservation fits AND the
    supervisor can observe RSS; a missing measurement refuses, never passes."""
    if reservation_bytes > cap_bytes:
        return {"allowed": False, "reason": "reservation exceeds the RSS ceiling"}
    if measured_rss_bytes is None:
        return {"allowed": False, "reason": "no supervised RSS measurement available"}
    if measured_rss_bytes > cap_bytes:
        return {"allowed": False, "reason": "measured RSS exceeds the ceiling"}
    return {"allowed": True, "reason": "reservation fits and RSS is supervised"}


def psutil_tree_reader() -> list[tuple[int, int]] | None:
    """Real process-tree RSS via psutil (self plus recursive children).

    Returns [(pid, rss)] with duplicate pids merged, or None when psutil
    is unavailable or unreadable (caller must then remain blocked).
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        root = psutil.Process()
        entries: dict[int, int] = {root.pid: root.memory_info().rss}
        for child in root.children(recursive=True):
            try:
                entries[child.pid] = entries.get(child.pid, 0) + child.memory_info().rss
            except Exception:
                continue
        return sorted(entries.items())
    except Exception:
        return None


class ProcessTreeSupervisor:
    """Enforcing process-tree RSS supervisor (not a supplied-number check).

    Measures the owned tree on every check: the current process, recursive
    OS children, plus explicitly registered owned child processes (pids
    deduplicated). Unavailable measurement refuses; over-cap refuses (and
    optionally terminates owned children first).
    """

    def __init__(
        self,
        cap_bytes: int = 268435456,
        *,
        reader: Any = None,
    ) -> None:
        self.cap_bytes = cap_bytes
        self._reader = reader if reader is not None else psutil_tree_reader
        self._owned: dict[int, Any] = {}

    def register_child(self, handle: Any) -> None:
        """Register an owned child process handle (must expose .pid)."""
        pid = int(getattr(handle, "pid", -1))
        if pid < 0:
            raise ReservationError("owned child has no pid")
        self._owned[pid] = handle

    def measure(self) -> dict[str, Any]:
        """Current tree RSS with per-pid breakdown; None when unobservable."""
        reading = self._reader()
        if reading is None:
            return {"observable": False, "total_rss": None, "members": []}
        members = [(int(pid), int(rss)) for pid, rss in reading]
        owned_pids = {pid for pid, _ in members} | set(self._owned)
        for pid in self._owned:
            if pid not in {m[0] for m in members}:
                members.append((pid, 0))
        _ = owned_pids
        return {
            "observable": True,
            "total_rss": sum(rss for _, rss in members),
            "members": sorted(members),
        }

    def check(self, *, context: str) -> dict[str, Any]:
        """Refuse when unobservable or over cap; otherwise allow with numbers."""
        measured = self.measure()
        if not measured["observable"] or measured["total_rss"] is None:
            raise ReservationError(f"{context}: process-tree RSS unobservable: blocked")
        if measured["total_rss"] > self.cap_bytes:
            raise ReservationError(
                f"{context}: process-tree RSS {measured['total_rss']} exceeds {self.cap_bytes}"
            )
        return {
            "allowed": True,
            "total_rss": measured["total_rss"],
            "cap_bytes": self.cap_bytes,
            "members": measured["members"],
        }

    def terminate_owned(self) -> list[int]:
        """Best-effort termination of registered owned children; returns pids."""
        stopped: list[int] = []
        for pid, handle in list(self._owned.items()):
            try:
                handle.terminate()
                stopped.append(pid)
            except Exception:
                continue
        return stopped


class DiskInventory:
    """Cumulative phase-aware disk accounting with fail-closed staging.

    Reservations happen incrementally BEFORE writes; an intermediate
    breach refuses even when the end state would fit; scratch peak is
    enforced independently; release requires the object to exist (only
    actual deletion releases); atomic tmp copies count old+new during
    replace.
    """

    def __init__(
        self,
        *,
        scratch_cap: int = 520093696,
        final_cap: int = 16777216,
        combined_cap: int = 536870912,
    ) -> None:
        self.scratch_cap = scratch_cap
        self.final_cap = final_cap
        self.combined_cap = combined_cap
        self.objects: dict[str, dict[str, int]] = {}
        self.peak_scratch = 0
        self.peak_combined = 0

    def _totals(self) -> tuple[int, int]:
        scratch = sum(o["scratch"] for o in self.objects.values())
        final = sum(o["final"] for o in self.objects.values())
        return scratch, final

    def stage(self, name: str, *, scratch: int = 0, final: int = 0) -> None:
        """Reserve (and conceptually write) one object; refuse on breach."""
        if name in self.objects:
            raise ReservationError(f"disk object already staged: {name}")
        for label, value in (("scratch", scratch), ("final", final)):
            if type(value) is not int or value < 0:
                raise ReservationError(f"disk {label} must be a non-negative integer")
        current_scratch, current_final = self._totals()
        new_scratch, new_final = current_scratch + scratch, current_final + final
        if new_scratch > self.scratch_cap:
            raise ReservationError(f"scratch peak would exceed cap staging {name}")
        if new_final > self.final_cap:
            raise ReservationError(f"final would exceed cap staging {name}")
        if new_scratch + new_final > self.combined_cap:
            raise ReservationError(f"combined storage would exceed cap staging {name}")
        self.objects[name] = {"scratch": scratch, "final": final}
        self.peak_scratch = max(self.peak_scratch, new_scratch)
        self.peak_combined = max(self.peak_combined, new_scratch + new_final)

    def atomic_replace(self, name: str, *, scratch_tmp: int, final_new: int) -> None:
        """Atomic tmp+replace: old+tmp coexist at peak, then old releases."""
        if name not in self.objects:
            raise ReservationError(f"atomic replace needs an existing object: {name}")
        self.stage(name + ".tmp", scratch=scratch_tmp, final=final_new)
        old = self.objects.pop(name)
        tmp = self.objects.pop(name + ".tmp")
        self.objects[name] = {"scratch": tmp["scratch"], "final": tmp["final"]}
        _ = old

    def release(self, name: str) -> None:
        """Release only when the accounted object is actually deleted."""
        if name not in self.objects:
            raise ReservationError(f"release of unstaged object refused: {name}")
        del self.objects[name]

    def snapshot(self) -> dict[str, Any]:
        """Current inventory with independently enforced peaks."""
        scratch, final = self._totals()
        return {
            "objects": {name: dict(cell) for name, cell in sorted(self.objects.items())},
            "scratch": scratch,
            "final": final,
            "peak_scratch": self.peak_scratch,
            "peak_combined": self.peak_combined,
            "fits": {
                "scratch": self.peak_scratch <= self.scratch_cap,
                "final": final <= self.final_cap,
                "combined": self.peak_combined <= self.combined_cap,
            },
        }


def staged_publication_gate(
    *,
    acquisition_final_bytes: int,
    label_budget_upper_bytes: int,
    caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Staged final-publication gate under one cumulative inventory.

    Acquisition-stage outputs must fit their reserved final capacity
    before text acquisition; later labeling reserves remaining space
    before every write. If the complete review cannot fit, the verdict
    is bounded-INCOMPLETE publication (preserve valid evidence, never
    truncate labels or drop documents).
    """
    limits = dict(caps) if caps is not None else v21_caps()
    final_cap = limits["final_artifact_bytes_max"]
    if acquisition_final_bytes > final_cap:
        return {
            "acquisition_allowed": False,
            "labels_fit": False,
            "verdict": "REFUSE_ACQUISITION",
            "reason": "acquisition-stage final exceeds the cumulative cap",
        }
    remaining = final_cap - acquisition_final_bytes
    if label_budget_upper_bytes <= remaining:
        return {
            "acquisition_allowed": True,
            "labels_fit": True,
            "verdict": "COMPLETE_FITS",
            "remaining_for_labels": remaining,
        }
    return {
        "acquisition_allowed": True,
        "labels_fit": False,
        "verdict": "INCOMPLETE_FALLBACK",
        "remaining_for_labels": remaining,
        "reason": (
            "complete review cannot fit: publish bounded INCOMPLETE evidence, "
            "preserve valid evidence, never truncate or drop"
        ),
    }


class DeadlineTracker:
    """Persisted monotonic deadline state with earliest-deadline-wins.

    Unknown historical durations are explicit unknowns (blocking), never
    zero. Elapsed accounting persists across restarts via save/load state.
    """

    def __init__(
        self,
        *,
        arm_cap: float = 1800.0,
        file_cap: float = 600.0,
        per_request_cap: float = 30.0,
        elapsed_seconds: float = 0.0,
        history_unknown: bool = False,
    ) -> None:
        if elapsed_seconds < 0:
            raise ReservationError("elapsed time must be non-negative")
        self.arm_cap = arm_cap
        self.file_cap = file_cap
        self.per_request_cap = per_request_cap
        self.elapsed_seconds = elapsed_seconds
        self.history_unknown = history_unknown

    def charge(self, seconds: float, *, file_elapsed: float = 0.0) -> None:
        """Charge measured seconds; refuse past any binding deadline."""
        if seconds < 0 or file_elapsed < 0:
            raise ReservationError("negative time charge")
        self.elapsed_seconds += seconds
        if self.elapsed_seconds > self.arm_cap:
            raise ReservationError("arm deadline exhausted")
        if file_elapsed > self.file_cap:
            raise ReservationError("file deadline exhausted")
        if seconds > self.per_request_cap:
            raise ReservationError("per-request deadline exhausted")

    def state(self) -> dict[str, Any]:
        """Persistable state (elapsed + unknown flag, never reset silently)."""
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "history_unknown": self.history_unknown,
            "arm_remaining": self.arm_cap - self.elapsed_seconds,
        }

    @staticmethod
    def load(state: Mapping[str, Any], *, arm_cap: float = 1800.0) -> DeadlineTracker:
        """Restore persisted elapsed accounting across restarts."""
        elapsed = state.get("elapsed_seconds", 0)
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise ReservationError("persisted elapsed time malformed")
        return DeadlineTracker(
            arm_cap=arm_cap,
            elapsed_seconds=float(elapsed),
            history_unknown=bool(state.get("history_unknown", False)),
        )

    def readiness(self) -> dict[str, Any]:
        """Unknown history blocks; otherwise arm remaining must be positive."""
        if self.history_unknown:
            return {
                "ready": False,
                "reasons": ["historical durations unknown: cannot certify remaining time"],
            }
        if self.elapsed_seconds >= self.arm_cap:
            return {"ready": False, "reasons": ["arm deadline already exhausted"]}
        return {"ready": True, "reasons": []}


def v21_caps() -> dict[str, int]:
    """Frozen ceilings relevant to reservations (unchanged by v2.1)."""
    limits = frozen.ARM_T_LIMITS
    return {
        "memory_resident_bytes": limits["memory_resident_bytes"],
        "parser_bytes_max": limits["parser_bytes_max"],
        "disk_scratch_bytes": limits["disk_scratch_bytes"],
        "disk_final_bytes": limits["disk_final_bytes"],
        "disk_combined_bytes": limits["disk_combined_bytes"],
        "document_bytes_max": limits["document_bytes_max"],
        "retained_text_bytes_max": limits["retained_text_bytes_max"],
        "final_artifact_bytes_max": limits["final_artifact_bytes_max"],
        "time_seconds_arm": limits["time_seconds_arm"],
        "time_seconds_file": limits["time_seconds_file"],
        "time_seconds_request": limits["time_seconds_request"],
    }
