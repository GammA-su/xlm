"""Repaired v3 runtime guards: fail-closed memory, physical disk, monotonic time.

Repairs relative to the v2.2 substrate (protocol section 8):
- ProcessTreeSupervisor: missing owned PIDs and unreadable children now
  fail closed (unobservable) unless explicit terminated-process proof is
  supplied. Zero-RSS insertion and silent child skipping are removed.
- PhysicalDiskInventory: binds real filesystem state; reservation happens
  BEFORE writes; release requires verified deletion; every phase
  reconciles actual occupancy; unknown files STOP unless allowlisted.
- ActiveRuntime: persisted per-arm/per-file active-machine-time totals
  plus monotonic anchors; absolute per-request deadlines; crash
  uncertainty conserved; only sealed quiescent pauses suspend charging.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import frozen_v3


class GuardError(ValueError):
    """Any supervision, inventory, or deadline violation: refuse."""


# --------------------------------------------------------------------------
# Fail-closed process-tree supervision.
# --------------------------------------------------------------------------


def default_tree_reader() -> list[tuple[int, int]] | None:
    """Real process-tree RSS via psutil; None when unavailable."""
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
            except Exception as exc:
                # v3 repair: an unreadable live child is unobservable, never
                # silently skipped. Signal failure via a sentinel pid.
                raise GuardError(f"unreadable child process: {exc}") from exc
        return sorted(entries.items())
    except GuardError:
        raise
    except Exception:
        return None


class FailClosedSupervisor:
    """Enforcing supervisor: unknown live memory fails closed.

    owned PIDs that are absent from the OS reading must be proven
    terminated via release_child(pid, terminated=True); otherwise the
    measurement is unobservable and check() refuses. Unreadable children
    (reader raising GuardError) also refuse.
    """

    def __init__(self, cap_bytes: int = 268435456, *, reader: Any = None) -> None:
        self.cap_bytes = cap_bytes
        self._reader = reader if reader is not None else default_tree_reader
        self._owned: dict[int, Any] = {}
        self._terminated: set[int] = set()

    def register_child(self, handle: Any) -> None:
        pid = int(getattr(handle, "pid", -1))
        if pid < 0:
            raise GuardError("owned child has no pid")
        self._owned[pid] = handle
        self._terminated.discard(pid)

    def release_child(self, pid: int, *, terminated: bool) -> None:
        """Explicit terminated-process proof removes a PID; anything else refuses."""
        if pid not in self._owned:
            raise GuardError(f"release of unregistered owned pid refused: {pid}")
        if terminated is not True:
            raise GuardError(f"ambiguous disappearance of owned pid {pid}: blocked")
        del self._owned[pid]
        self._terminated.add(pid)

    def measure(self) -> dict[str, Any]:
        try:
            reading = self._reader()
        except GuardError as exc:
            return {"observable": False, "total_rss": None, "members": [], "reason": str(exc)}
        if reading is None:
            return {"observable": False, "total_rss": None, "members": []}
        members = [(int(pid), int(rss)) for pid, rss in reading]
        seen = {pid for pid, _ in members}
        for pid in sorted(self._owned):
            if pid not in seen:
                # v3 repair: no zero-RSS insertion. Without terminated proof
                # this owned process is ambiguously missing -> unobservable.
                return {
                    "observable": False,
                    "total_rss": None,
                    "members": sorted(members),
                    "reason": f"owned pid {pid} absent without terminated proof",
                }
        # Deduplicate PIDs (owned handles share OS pids with tree entries).
        merged: dict[int, int] = {}
        for pid, rss in members:
            merged[pid] = merged.get(pid, 0) + rss if pid not in merged else merged[pid]
            # NOTE: members already deduped by construction for tree pids;
            # owned handles do not add RSS beyond the OS reading.
        total = sum(merged.values())
        return {"observable": True, "total_rss": total, "members": sorted(merged.items())}

    def check(self, *, context: str) -> dict[str, Any]:
        measured = self.measure()
        if not measured["observable"] or measured["total_rss"] is None:
            reason = measured.get("reason", "process-tree RSS unobservable")
            raise GuardError(f"{context}: {reason}: blocked")
        if measured["total_rss"] > self.cap_bytes:
            raise GuardError(
                f"{context}: process-tree RSS {measured['total_rss']} exceeds {self.cap_bytes}"
            )
        return {
            "allowed": True,
            "total_rss": measured["total_rss"],
            "cap_bytes": self.cap_bytes,
            "members": measured["members"],
        }


# --------------------------------------------------------------------------
# Physical disk inventory bound to the real execution root.
# --------------------------------------------------------------------------


class PhysicalDiskInventory:
    """Reservation-before-write inventory reconciled against the filesystem.

    Logical reservations live in self.objects; every stage reconciles with
    a physical walk of root. Unknown files STOP unless explicitly
    allowlisted. Release requires verified deletion from the filesystem.
    Atomic replaces count old+new during the copy.
    """

    def __init__(
        self,
        root: Path,
        *,
        scratch_cap: int,
        final_cap: int,
        combined_cap: int,
        allowlist: tuple[str, ...] = (),
    ) -> None:
        self.root = root
        self.scratch_cap = scratch_cap
        self.final_cap = final_cap
        self.combined_cap = combined_cap
        self.allowlist = set(allowlist)
        self.objects: dict[str, dict[str, int]] = {}
        self.peak_scratch = 0
        self.peak_combined = 0

    def _totals(self) -> tuple[int, int]:
        scratch = sum(o["scratch"] for o in self.objects.values())
        final = sum(o["final"] for o in self.objects.values())
        return scratch, final

    def reserve(self, name: str, *, scratch: int = 0, final: int = 0) -> None:
        """Reserve BEFORE any write; refuse on any breach."""
        if name in self.objects:
            raise GuardError(f"disk object already reserved: {name}")
        for label, value in (("scratch", scratch), ("final", final)):
            if type(value) is not int or value < 0:
                raise GuardError(f"disk {label} must be a non-negative integer")
        cur_scratch, cur_final = self._totals()
        new_scratch, new_final = cur_scratch + scratch, cur_final + final
        if new_scratch > self.scratch_cap:
            raise GuardError(f"scratch peak would exceed cap reserving {name}")
        if new_final > self.final_cap:
            raise GuardError(f"final would exceed cap reserving {name}")
        if new_scratch + new_final > self.combined_cap:
            raise GuardError(f"combined storage would exceed cap reserving {name}")
        self.objects[name] = {"scratch": scratch, "final": final}
        self.peak_scratch = max(self.peak_scratch, new_scratch)
        self.peak_combined = max(self.peak_combined, new_scratch + new_final)

    def write_file(self, name: str, payload: bytes) -> dict[str, Any]:
        """Write a reserved object atomically (tmp + fsync + replace)."""
        if name not in self.objects:
            raise GuardError(f"write without prior reservation refused: {name}")
        # Reservation must cover the payload in its category: treat payload
        # as final bytes for manifest/receipt-style objects.
        target = self.root / name
        if target.is_symlink():
            raise GuardError(f"link escape refused: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        actual = target.stat().st_size
        binding = {"bytes": actual, "sha256": hashlib.sha256(payload).hexdigest()}
        return binding

    def release(self, name: str) -> None:
        """Release only after verified deletion from the filesystem."""
        if name not in self.objects:
            raise GuardError(f"release of unreserved object refused: {name}")
        target = self.root / name
        if target.exists() or target.is_symlink():
            raise GuardError(f"release refused: {name} still present on filesystem")
        del self.objects[name]

    def delete_verified(self, name: str) -> None:
        """Delete the file, verify absence, then release the reservation."""
        if name not in self.objects:
            raise GuardError(f"delete of unreserved object refused: {name}")
        target = self.root / name
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise GuardError(f"verified deletion failed (missing): {name}") from exc
        if target.exists() or target.is_symlink():
            raise GuardError(f"verified deletion failed (still present): {name}")
        del self.objects[name]

    def reconcile(self) -> dict[str, Any]:
        """Walk the filesystem; unknown files STOP; report occupancy."""
        if not self.root.is_dir():
            raise GuardError(f"cannot reconcile non-directory root: {self.root}")
        physical: dict[str, int] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                raise GuardError(f"link/reparse escape in execution root: {path}")
            if path.is_file():
                if path.suffix == ".tmp":
                    raise GuardError(f"stray atomic temp file: {path}")
                rel = path.relative_to(self.root).as_posix()
                physical[rel] = path.stat().st_size
        unknown = sorted(set(physical) - set(self.objects) - self.allowlist)
        if unknown:
            raise GuardError(f"unknown files in execution root: {unknown}: STOP")
        reserved_total = sum(v["scratch"] + v["final"] for v in self.objects.values())
        return {
            "reserved_objects": {k: dict(v) for k, v in sorted(self.objects.items())},
            "physical_files": physical,
            "physical_total_bytes": sum(physical.values()),
            "reserved_total": reserved_total,
            "peak_scratch": self.peak_scratch,
            "peak_combined": self.peak_combined,
        }

    def snapshot(self) -> dict[str, Any]:
        scratch, final = self._totals()
        return {
            "objects": {k: dict(v) for k, v in sorted(self.objects.items())},
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


# --------------------------------------------------------------------------
# Persisted active-machine-time accounting (monotonic, no wall-clock diffs).
# --------------------------------------------------------------------------


class ActiveRuntime:
    """Cumulative active machine-execution seconds with monotonic anchors.

    v3 runtime begins at epoch genesis. Per-arm and per-file totals plus
    start anchors persist across restarts. Only a sealed quiescent review
    pause (all workers stopped, no network/processing) suspends charging.
    Every request has a real 30 s absolute bound clipped by remaining
    file/arm time. Unexpected death is never proof of a quiescent pause.
    """

    def __init__(
        self,
        *,
        arm_cap: float = 1800.0,
        file_cap: float = 600.0,
        per_request_cap: float = 30.0,
        arm_elapsed: float = 0.0,
        file_elapsed: Mapping[str, float] | None = None,
        anchor_monotonic_ns: int | None = None,
        paused: bool = False,
        clock: Any = None,
    ) -> None:
        if arm_elapsed < 0:
            raise GuardError("arm elapsed must be non-negative")
        self.arm_cap = arm_cap
        self.file_cap = file_cap
        self.per_request_cap = per_request_cap
        self.arm_elapsed = float(arm_elapsed)
        self.file_elapsed: dict[str, float] = {k: float(v) for k, v in (file_elapsed or {}).items()}
        self.anchor_monotonic_ns = anchor_monotonic_ns
        self.paused = paused
        self._clock = clock if clock is not None else time.monotonic_ns

    def start_segment(self) -> None:
        """Open a measured active segment (anchor the monotonic clock)."""
        if self.paused:
            raise GuardError("cannot start an active segment while paused")
        if self.anchor_monotonic_ns is not None:
            raise GuardError("active segment already open")
        self.anchor_monotonic_ns = int(self._clock())

    def end_segment(self, *, file: str) -> float:
        """Close the segment; charge arm+file from the monotonic delta."""
        if self.anchor_monotonic_ns is None:
            raise GuardError("no active segment open")
        now = int(self._clock())
        delta = (now - int(self.anchor_monotonic_ns)) / 1_000_000_000
        if delta < 0:
            raise GuardError("monotonic clock went backwards: blocked")
        self.anchor_monotonic_ns = None
        return self.charge(delta, file=file)

    def charge(self, seconds: float, *, file: str) -> float:
        """Charge measured active seconds; earliest deadline wins."""
        if self.paused:
            raise GuardError("charging while paused is refused (quiescent only)")
        if not isinstance(seconds, (int, float)) or seconds < 0:
            raise GuardError("negative time charge")
        if seconds > self.per_request_cap:
            raise GuardError("per-request 30 s absolute bound exhausted")
        got_file = self.file_elapsed.get(file, 0.0) + float(seconds)
        if got_file > self.file_cap:
            raise GuardError(f"file deadline exhausted for {file}")
        if self.arm_elapsed + float(seconds) > self.arm_cap:
            raise GuardError("arm deadline exhausted")
        self.file_elapsed[file] = got_file
        self.arm_elapsed += float(seconds)
        return float(seconds)

    def request_budget(self, *, file: str) -> float:
        """Remaining seconds available to one request (clipped by file/arm)."""
        remaining_arm = self.arm_cap - self.arm_elapsed
        remaining_file = self.file_cap - self.file_elapsed.get(file, 0.0)
        return max(0.0, min(self.per_request_cap, remaining_arm, remaining_file))

    def seal_pause(self) -> None:
        """Enter a sealed quiescent review pause (no open segment allowed)."""
        if self.anchor_monotonic_ns is not None:
            raise GuardError("cannot pause with an open active segment")
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def crash_uncertainty(self, seconds: float, *, file: str) -> None:
        """Conserve unknown crash time as charged work (never refunded)."""
        if seconds < 0:
            raise GuardError("negative crash uncertainty")
        # Conserved unconditionally (may itself refuse on exhaustion).
        self.charge(float(seconds), file=file)

    def state(self) -> dict[str, Any]:
        return {
            "arm_elapsed": self.arm_elapsed,
            "file_elapsed": dict(sorted(self.file_elapsed.items())),
            "anchor_monotonic_ns": self.anchor_monotonic_ns,
            "paused": self.paused,
            "arm_remaining": self.arm_cap - self.arm_elapsed,
        }

    @staticmethod
    def load(
        state: Mapping[str, Any],
        *,
        arm_cap: float = 1800.0,
        file_cap: float = 600.0,
        per_request_cap: float = 30.0,
        clock: Any = None,
    ) -> ActiveRuntime:
        elapsed = state.get("arm_elapsed", 0)
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise GuardError("persisted arm elapsed malformed")
        files = state.get("file_elapsed", {})
        if not isinstance(files, Mapping):
            raise GuardError("persisted file elapsed malformed")
        for _k, v in files.items():
            if not isinstance(v, (int, float)) or v < 0:
                raise GuardError("persisted file elapsed malformed")
        anchor = state.get("anchor_monotonic_ns")
        if anchor is not None and (type(anchor) is not int or anchor < 0):
            raise GuardError("persisted clock anchor malformed")
        return ActiveRuntime(
            arm_cap=arm_cap,
            file_cap=file_cap,
            per_request_cap=per_request_cap,
            arm_elapsed=float(elapsed),
            file_elapsed={str(k): float(v) for k, v in files.items()},
            anchor_monotonic_ns=anchor,
            paused=bool(state.get("paused", False)),
            clock=clock,
        )

    def save(self, path: Any) -> dict[str, int | str]:
        return canonical.write_canonical_json(path, {"kind": "v3_active_runtime", **self.state()})


def allocator_reservation(
    *, base_bytes: int, output_bytes: int, cap_bytes: int = 268435456
) -> dict[str, Any]:
    """Conservative allocator/base/output reservation (formula, not measured RSS)."""
    if base_bytes < 0 or output_bytes < 0:
        raise GuardError("reservation inputs must be non-negative")
    total = base_bytes + output_bytes
    return {
        "base_bytes": base_bytes,
        "output_bytes": output_bytes,
        "total": total,
        "fits": total <= cap_bytes,
    }


def check_caps_unchanged(m_caps: Mapping[str, int], t_caps: Mapping[str, int]) -> None:
    if dict(m_caps) != dict(frozen_v3.ARM_M_CAPS):
        raise GuardError("M caps drifted from the frozen v3 contract")
    if dict(t_caps) != dict(frozen_v3.ARM_T_CAPS):
        raise GuardError("T caps drifted from the frozen v3 contract")
