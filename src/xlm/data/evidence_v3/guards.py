"""v3 runtime guards: enforcing memory, contained disk, conserved time.

Repairs relative to the earlier v3 substrate (Astra remediation):

- Memory: ``FailClosedSupervisor`` plus an authoritative
  ``OwnedProcessRegistry``. Proof of exit is a registry-issued ``ExitProof``
  bound to a per-registration nonce; a caller-supplied boolean can never
  create one. ``enforce_around`` wraps Phase-P work with pre/post and
  periodic checks so cap violations stop further work.
- Disk: ``PhysicalDiskInventory`` resolves every path strictly inside the
  execution root (rejects ``..``, absolute/external paths, symlink/reparse
  and ancestor redirection, alternate volumes). Reservation must cover the
  actual write or the operation refuses; atomic replace counts old+temp
  simultaneously; real peak physical occupancy is tracked from filesystem
  walks; inventory state persists; unknown files STOP; release follows
  verified deletion; imports are allowlisted, hash-verified, disk-counted.
- Runtime: ``ActiveRuntime`` includes open-segment elapsed in every budget
  query, never clears an anchor without charging first, conserves crash
  time, and pauses only for an explicit sealed quiescent review state.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import frozen_v3


class GuardError(ValueError):
    """Any supervision, inventory, or deadline violation: refuse."""


# --------------------------------------------------------------------------
# Fail-closed process-tree supervision with authoritative lifecycle registry.
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
                raise GuardError(f"unreadable child process: {exc}") from exc
        return sorted(entries.items())
    except GuardError:
        raise
    except Exception:
        return None


@dataclass(frozen=True)
class ExitProof:
    """Registry-issued proof that an owned PID has authoritatively exited.

    Only ``OwnedProcessRegistry.reap`` can mint a valid proof: the nonce
    must match the registry's per-registration record. Directly
    constructed proofs carry the wrong nonce and are refused.
    """

    pid: int
    nonce: int


class OwnedProcessRegistry:
    """Authoritative owned-process lifecycle registry.

    ``register`` records ACTIVE with a fresh nonce. ``reap`` consults the
    liveness probe (default: psutil pid existence); a live PID refuses,
    an exited PID mints an ``ExitProof``. ``release`` consumes a valid
    proof. No boolean flag can substitute for a proof.
    """

    def __init__(self, *, is_alive: Callable[[int], bool] | None = None) -> None:
        self._nonces: dict[int, int] = {}
        self._counter = itertools.count(1)
        self._is_alive = is_alive if is_alive is not None else _default_is_alive

    def register(self, pid: int) -> None:
        if type(pid) is not int or pid < 0:
            raise GuardError("owned pid must be a non-negative integer")
        self._nonces[pid] = next(self._counter)

    def reap(self, pid: int) -> ExitProof:
        if pid not in self._nonces:
            raise GuardError(f"reap of unregistered owned pid refused: {pid}")
        try:
            alive = self._is_alive(pid)
        except Exception as exc:
            raise GuardError(f"ambiguous liveness of owned pid {pid}: blocked") from exc
        if alive:
            raise GuardError(f"owned pid {pid} is still alive: no exit proof")
        return ExitProof(pid=pid, nonce=self._nonces[pid])

    def release(self, proof: ExitProof) -> None:
        if not isinstance(proof, ExitProof):
            raise GuardError("owned-process release requires a registry ExitProof, never a boolean")
        expected = self._nonces.get(proof.pid)
        if expected is None or proof.nonce != expected:
            raise GuardError(f"invalid exit proof for pid {proof.pid}: blocked")
        del self._nonces[proof.pid]

    def active(self) -> tuple[int, ...]:
        return tuple(sorted(self._nonces))


def _default_is_alive(pid: int) -> bool:
    try:
        import psutil
    except ImportError as exc:
        raise GuardError("no liveness probe available: blocked") from exc
    return psutil.pid_exists(pid)


class FailClosedSupervisor:
    """Enforcing supervisor: unknown live memory fails closed.

    Owned PIDs come from an ``OwnedProcessRegistry``: PIDs with active
    registrations must appear in the OS reading, otherwise the measurement
    is unobservable. Unreadable children (reader raising GuardError) also
    refuse. The legacy ``release_child(pid, terminated=...)`` helper is
    retained only for pre-remediation callers and is never consulted by the
    executor path, which requires registry proofs.
    """

    def __init__(
        self,
        cap_bytes: int = 268435456,
        *,
        reader: Any = None,
        registry: OwnedProcessRegistry | None = None,
    ) -> None:
        self.cap_bytes = cap_bytes
        self._reader = reader if reader is not None else default_tree_reader
        self._owned: dict[int, Any] = {}
        self._terminated: set[int] = set()
        self._registry = registry

    def register_child(self, handle: Any) -> None:
        pid = int(getattr(handle, "pid", -1))
        if pid < 0:
            raise GuardError("owned child has no pid")
        self._owned[pid] = handle
        self._terminated.discard(pid)

    def release_child(self, pid: int, *, terminated: bool) -> None:
        """Legacy release (pre-remediation callers only, not executor proof)."""
        if pid not in self._owned:
            raise GuardError(f"release of unregistered owned pid refused: {pid}")
        if terminated is not True:
            raise GuardError(f"ambiguous disappearance of owned pid {pid}: blocked")
        del self._owned[pid]
        self._terminated.add(pid)

    def _required_pids(self) -> set[int]:
        required = {pid for pid in self._owned if pid not in self._terminated}
        if self._registry is not None:
            required |= set(self._registry.active())
        return required

    def measure(self) -> dict[str, Any]:
        try:
            reading = self._reader()
        except GuardError as exc:
            return {"observable": False, "total_rss": None, "members": [], "reason": str(exc)}
        if reading is None:
            return {"observable": False, "total_rss": None, "members": []}
        members = [(int(pid), int(rss)) for pid, rss in reading]
        seen = {pid for pid, _ in members}
        for pid in sorted(self._required_pids()):
            if pid not in seen:
                return {
                    "observable": False,
                    "total_rss": None,
                    "members": sorted(members),
                    "reason": f"owned pid {pid} absent without authoritative release",
                }
        merged: dict[int, int] = {}
        for pid, rss in members:
            if pid not in merged:
                merged[pid] = rss
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

    def enforce_around(
        self, work: Callable[[], Any], *, context: str, monitor: Callable[[], None] | None = None
    ) -> Any:
        """Run ``work`` only while supervision allows; check before and after.

        An optional ``monitor`` hook runs between the checks so the executor
        can interleave periodic supervision during long transfers. Any
        refusal propagates and the caller must not continue metered work.
        """
        self.check(context=f"{context}:pre")
        try:
            result = work()
        finally:
            if monitor is not None:
                monitor()
            self.check(context=f"{context}:post")
        return result


# --------------------------------------------------------------------------
# Contained physical disk inventory bound to the execution root.
# --------------------------------------------------------------------------


def _resolve_inside(root: Path, name: str) -> Path:
    """Resolve ``name`` strictly inside ``root`` or refuse."""
    if not isinstance(name, str) or not name or name.startswith("\0"):
        raise GuardError("disk name must be a non-empty string")
    pure = PurePath(name)
    if pure.is_absolute() or pure.drive:
        raise GuardError(f"absolute/external path escape refused: {name}")
    if ".." in pure.parts:
        raise GuardError(f"parent-directory escape refused: {name}")
    root_abs = os.path.abspath(str(root))
    target_abs = os.path.abspath(os.path.join(root_abs, name))
    if os.path.normcase(target_abs) != os.path.normcase(root_abs) and not os.path.normcase(
        target_abs
    ).startswith(os.path.normcase(root_abs) + os.sep):
        raise GuardError(f"root escape refused: {name}")
    if os.path.splitdrive(target_abs)[0].lower() != os.path.splitdrive(root_abs)[0].lower():
        raise GuardError(f"alternate-volume escape refused: {name}")
    # Ancestor reparse redirection: every existing ancestor must be real.
    cursor = Path(target_abs)
    base = Path(root_abs)
    while cursor != base and base in cursor.parents:
        if cursor.is_symlink():
            raise GuardError(f"symlink/reparse escape refused: {name}")
        cursor = cursor.parent
    for ancestor in (root_abs, os.path.dirname(root_abs)):
        if os.path.islink(ancestor):
            raise GuardError("ancestor reparse redirection refused")
    final = Path(target_abs)
    if final.is_symlink():
        raise GuardError(f"symlink/reparse escape refused: {name}")
    return final


class PhysicalDiskInventory:
    """Reservation-before-write inventory reconciled against the filesystem.

    Reservations happen BEFORE writes; the reservation total
    (scratch + final) must cover the actual resulting bytes or the write
    refuses and never reports fits=true. Atomic replaces count old and
    temp simultaneously against a real peak-physical high-water mark
    obtained from filesystem walks. Unknown files STOP unless explicitly
    allowlisted. Release requires verified deletion. Imports are
    allowlisted, hash-verified, and disk-counted.
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
        self.actual: dict[str, int] = {}
        self.peak_scratch = 0
        self.peak_combined = 0
        self.peak_physical = 0
        self.incomplete: list[str] = []

    def _totals(self) -> tuple[int, int]:
        scratch = sum(o["scratch"] for o in self.objects.values())
        final = sum(o["final"] for o in self.objects.values())
        return scratch, final

    def _physical_total(self) -> int:
        total = 0
        if self.root.is_dir():
            for path in sorted(self.root.rglob("*")):
                if path.is_symlink():
                    raise GuardError(f"link/reparse escape in execution root: {path}")
                if path.is_file() and path.suffix != ".tmp":
                    total += path.stat().st_size
        self.peak_physical = max(self.peak_physical, total)
        return total

    def reserve(self, name: str, *, scratch: int = 0, final: int = 0) -> None:
        """Reserve BEFORE any write; refuse on any breach."""
        _resolve_inside(self.root, name)
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
        """Write a reserved object atomically; reservation must cover actuals."""
        if name not in self.objects:
            raise GuardError(f"write without prior reservation refused: {name}")
        target = _resolve_inside(self.root, name)
        reserved = self.objects[name]["scratch"] + self.objects[name]["final"]
        if len(payload) > reserved:
            self.incomplete.append(name)
            raise GuardError(
                f"actual write {len(payload)} exceeds reservation {reserved} for {name}: "
                "refused, marked incomplete"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        actual = target.stat().st_size
        if actual > reserved:
            self.incomplete.append(name)
            raise GuardError(f"actual occupancy {actual} exceeds reservation for {name}")
        self.actual[name] = actual
        self._physical_total()
        return {"bytes": actual, "sha256": hashlib.sha256(payload).hexdigest()}

    def atomic_replace(
        self, name: str, payload: bytes, *, scratch_tmp: int, final_new: int
    ) -> dict[str, Any]:
        """Atomic tmp+replace counting old and new simultaneously at peak."""
        if name not in self.objects:
            raise GuardError(f"atomic replace needs an existing object: {name}")
        old_actual = self.actual.get(name, 0)
        if len(payload) > scratch_tmp + final_new:
            self.incomplete.append(name)
            raise GuardError(f"replacement payload exceeds its reservation for {name}")
        cur_scratch, cur_final = self._totals()
        peak_during = cur_scratch + scratch_tmp + cur_final + final_new
        if peak_during > self.combined_cap:
            raise GuardError(f"combined storage would exceed cap replacing {name}")
        self.peak_scratch = max(self.peak_scratch, cur_scratch + scratch_tmp)
        self.peak_combined = max(self.peak_combined, peak_during)
        self.peak_physical = max(self.peak_physical, self._physical_total() + len(payload))
        target = _resolve_inside(self.root, name)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        old = self.objects.pop(name)
        _ = old
        self.objects[name] = {"scratch": scratch_tmp, "final": final_new}
        self.actual[name] = target.stat().st_size
        self._physical_total()
        void = old_actual
        _ = void
        return {"bytes": self.actual[name], "sha256": hashlib.sha256(payload).hexdigest()}

    def import_allowlisted(
        self, name: str, payload: bytes, *, expected_sha256: str, scratch: int = 0, final: int = 0
    ) -> dict[str, Any]:
        """Import hash-verified allowlisted metadata, disk-counted."""
        if name not in self.allowlist:
            raise GuardError(f"import of non-allowlisted artifact refused: {name}")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise GuardError(f"import hash mismatch for {name}")
        self.reserve(name, scratch=scratch, final=final)
        return self.write_file(name, payload)

    def release(self, name: str) -> None:
        """Release only after verified deletion from the filesystem."""
        if name not in self.objects:
            raise GuardError(f"release of unreserved object refused: {name}")
        target = _resolve_inside(self.root, name)
        if target.exists() or target.is_symlink():
            raise GuardError(f"release refused: {name} still present on filesystem")
        del self.objects[name]
        self.actual.pop(name, None)

    def delete_verified(self, name: str) -> None:
        """Delete the file, verify absence, then release the reservation."""
        if name not in self.objects:
            raise GuardError(f"delete of unreserved object refused: {name}")
        target = _resolve_inside(self.root, name)
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise GuardError(f"verified deletion failed (missing): {name}") from exc
        if target.exists() or target.is_symlink():
            raise GuardError(f"verified deletion failed (still present): {name}")
        del self.objects[name]
        self.actual.pop(name, None)
        self._physical_total()

    def reconcile(self) -> dict[str, Any]:
        """Walk the filesystem; unknown/oversize files STOP; report occupancy."""
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
        for name, size in physical.items():
            if name in self.objects:
                reserved = self.objects[name]["scratch"] + self.objects[name]["final"]
                if size > reserved:
                    raise GuardError(
                        f"actual occupancy {size} exceeds reservation {reserved} for {name}"
                    )
        self.peak_physical = max(self.peak_physical, sum(physical.values()))
        reserved_total = sum(v["scratch"] + v["final"] for v in self.objects.values())
        return {
            "reserved_objects": {k: dict(v) for k, v in sorted(self.objects.items())},
            "physical_files": physical,
            "physical_total_bytes": sum(physical.values()),
            "reserved_total": reserved_total,
            "peak_scratch": self.peak_scratch,
            "peak_combined": self.peak_combined,
            "peak_physical": self.peak_physical,
            "fits": {
                "scratch": self.peak_scratch <= self.scratch_cap,
                "combined": self.peak_combined <= self.combined_cap,
                "physical": self.peak_physical <= self.combined_cap,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        scratch, final = self._totals()
        return {
            "objects": {k: dict(v) for k, v in sorted(self.objects.items())},
            "actual": dict(sorted(self.actual.items())),
            "scratch": scratch,
            "final": final,
            "peak_scratch": self.peak_scratch,
            "peak_combined": self.peak_combined,
            "peak_physical": self.peak_physical,
            "incomplete": list(self.incomplete),
            "fits": {
                "scratch": self.peak_scratch <= self.scratch_cap,
                "final": final <= self.final_cap,
                "combined": self.peak_combined <= self.combined_cap,
            },
        }

    def save_state(self, path: Path) -> dict[str, int | str]:
        body = {
            "kind": "essential_web_v3_disk_inventory",
            "epoch_id": frozen_v3.EPOCH_ID,
            "root": self.root.as_posix(),
            "objects": {k: dict(v) for k, v in sorted(self.objects.items())},
            "actual": dict(sorted(self.actual.items())),
            "peak_scratch": self.peak_scratch,
            "peak_combined": self.peak_combined,
            "peak_physical": self.peak_physical,
            "incomplete": list(self.incomplete),
        }
        return canonical.write_canonical_json(path, body)

    @staticmethod
    def load_state(path: Path, root: Path, *, caps: Mapping[str, int]) -> PhysicalDiskInventory:
        body = canonical.loads_bytes_strict(Path(path).read_bytes())
        if not isinstance(body, dict) or body.get("epoch_id") != frozen_v3.EPOCH_ID:
            raise GuardError("disk inventory binds a different epoch")
        inv = PhysicalDiskInventory(
            root,
            scratch_cap=int(caps["scratch"]),
            final_cap=int(caps["final"]),
            combined_cap=int(caps["combined"]),
        )
        inv.objects = {str(k): dict(v) for k, v in dict(body.get("objects", {})).items()}
        inv.actual = {str(k): int(v) for k, v in dict(body.get("actual", {})).items()}
        inv.peak_scratch = int(body.get("peak_scratch", 0))
        inv.peak_combined = int(body.get("peak_combined", 0))
        inv.peak_physical = int(body.get("peak_physical", 0))
        inv.incomplete = list(body.get("incomplete", []))
        return inv


# --------------------------------------------------------------------------
# Conserved active-machine-time accounting (monotonic, no wall-clock diffs).
# --------------------------------------------------------------------------


class ActiveRuntime:
    """Cumulative active machine-execution seconds with monotonic anchors.

    v3 runtime begins at epoch genesis. Per-arm and per-file totals, the
    open-segment anchor, and request in-flight state persist across
    restarts. Budget queries include currently-open-segment elapsed.
    Anchors are never cleared without charging first: a refused
    ``end_segment`` keeps the anchor open and prior elapsed intact. Crash
    time is conserved via ``conserve_crash_time``. Pauses require an
    explicit sealed quiescent review state and refuse while any segment
    is open.
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
        open_file: str | None = None,
        paused: bool = False,
        conserved_crash_seconds: float = 0.0,
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
        self.open_file = open_file
        self.paused = paused
        self.conserved_crash_seconds = float(conserved_crash_seconds)
        self._clock = clock if clock is not None else time.monotonic_ns

    def _open_elapsed(self) -> float:
        if self.anchor_monotonic_ns is None:
            return 0.0
        now = int(self._clock())
        delta = (now - int(self.anchor_monotonic_ns)) / 1_000_000_000
        if delta < 0:
            raise GuardError("monotonic clock went backwards: blocked")
        return delta

    def start_segment(self, *, file: str) -> None:
        """Open a measured active segment for one file."""
        if self.paused:
            raise GuardError("cannot start an active segment while paused")
        if self.anchor_monotonic_ns is not None:
            raise GuardError("active segment already open")
        self.anchor_monotonic_ns = int(self._clock())
        self.open_file = file

    def end_segment(self, *, file: str) -> float:
        """Close the segment, charging arm+file from the monotonic delta.

        On refusal the anchor stays open and previously charged elapsed is
        retained, so runtime is conserved even when the operation fails.
        """
        if self.anchor_monotonic_ns is None:
            raise GuardError("no active segment open")
        if self.open_file is not None and file != self.open_file:
            raise GuardError("end_segment file does not match the open segment")
        now = int(self._clock())
        delta = (now - int(self.anchor_monotonic_ns)) / 1_000_000_000
        if delta < 0:
            raise GuardError("monotonic clock went backwards: blocked")
        charged = self.charge(delta, file=file)
        self.anchor_monotonic_ns = None
        self.open_file = None
        return charged

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
        """Remaining seconds for one request, including open-segment elapsed.

        An already-open segment that has consumed its whole per-request
        allowance leaves zero remaining: over-budget work refuses instead
        of starting another 30 s window.
        """
        extra = self._open_elapsed()
        open_file = self.open_file or file
        remaining_arm = self.arm_cap - self.arm_elapsed - extra
        remaining_file = self.file_cap - self.file_elapsed.get(file, 0.0)
        consumed_by_open = extra if open_file == file else 0.0
        if consumed_by_open >= self.per_request_cap:
            return 0.0
        if open_file == file:
            remaining_file -= extra
        if remaining_arm <= 0 or remaining_file <= 0:
            return 0.0
        return max(0.0, min(self.per_request_cap - consumed_by_open, remaining_arm, remaining_file))

    def seal_pause(self, *, sealed_quiescent: bool = False) -> None:
        """Enter a sealed quiescent review pause.

        Requires the protocol-defined sealed state flag and refuses while
        any request/file segment is open. Arbitrary pauses are refused.
        """
        if sealed_quiescent is not True:
            raise GuardError("pause requires the sealed quiescent review state")
        if self.anchor_monotonic_ns is not None:
            raise GuardError("cannot pause with an open active segment")
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def crash_uncertainty(self, seconds: float, *, file: str) -> None:
        """Conserve unknown crash time as charged work (never refunded)."""
        if seconds < 0:
            raise GuardError("negative crash uncertainty")
        self.charge(float(seconds), file=file)
        self.conserved_crash_seconds += float(seconds)

    def conserve_restart_open_segment(self) -> float:
        """Charge the persisted open anchor at restart, then close it.

        A restart with an open segment conserves the elapsed time from the
        anchor to now as crash time instead of dropping it.
        """
        if self.anchor_monotonic_ns is None:
            return 0.0
        now = int(self._clock())
        delta = (now - int(self.anchor_monotonic_ns)) / 1_000_000_000
        if delta < 0:
            raise GuardError("monotonic clock went backwards: blocked")
        conserved = self.charge(delta, file=self.open_file or "restart-recovery")
        self.conserved_crash_seconds += float(delta)
        self.anchor_monotonic_ns = None
        self.open_file = None
        return conserved

    def state(self) -> dict[str, Any]:
        return {
            "arm_elapsed": self.arm_elapsed,
            "file_elapsed": dict(sorted(self.file_elapsed.items())),
            "anchor_monotonic_ns": self.anchor_monotonic_ns,
            "open_file": self.open_file,
            "paused": self.paused,
            "conserved_crash_seconds": self.conserved_crash_seconds,
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
        open_file = state.get("open_file")
        if open_file is not None and not isinstance(open_file, str):
            raise GuardError("persisted open file malformed")
        conserved = state.get("conserved_crash_seconds", 0.0)
        if not isinstance(conserved, (int, float)) or conserved < 0:
            raise GuardError("persisted conserved crash time malformed")
        return ActiveRuntime(
            arm_cap=arm_cap,
            file_cap=file_cap,
            per_request_cap=per_request_cap,
            arm_elapsed=float(elapsed),
            file_elapsed={str(k): float(v) for k, v in files.items()},
            anchor_monotonic_ns=anchor,
            open_file=open_file,
            paused=bool(state.get("paused", False)),
            conserved_crash_seconds=float(conserved),
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


def check_parser_reservation(parser_bytes: int, *, cap_bytes: int = 33554432) -> None:
    """Parser workspace must fit its frozen ceiling before any parse."""
    if type(parser_bytes) is not int or parser_bytes < 0:
        raise GuardError("parser reservation must be a non-negative integer")
    if parser_bytes > cap_bytes:
        raise GuardError("parser reservation exceeds its ceiling")
