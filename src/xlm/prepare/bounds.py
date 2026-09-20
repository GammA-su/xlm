"""Preparation limits using the existing journal and capacity manager."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from xlm.artifacts.manifest import bounded_children, ensure_plain_path
from xlm.data.acquisition.disk import DiskCeilingExceededError, StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.records import inspect_records
from xlm.data.sources.transport import BudgetExhaustedError
from xlm.prepare.config import PrepareBudgets, PrepareConfig


class PrepareBounds:
    def __init__(
        self, config: PrepareConfig, output_root: Path, home: Path, config_dir: Path | None = None
    ) -> None:
        from xlm.prepare.integrity import path_digest
        from xlm.prepare.planner import resolve_variables

        self.limits = PrepareBudgets.model_validate(config.budgets)
        self.output_root, self.home = output_root, home
        self.control = output_root / ".accounting"
        self.spool = self.control / "logs"
        ensure_plain_path(output_root)
        ensure_plain_path(home)
        repo = Path(__file__).resolve().parents[3]
        variables = {
            "repo": str(repo),
            "home": str(home),
            "output_root": str(output_root),
            "config_dir": str(config_dir or repo),
        }
        inputs: dict[str, str] = {}
        produced: list[Path] = []
        for stage in config.stages:
            for raw in [*stage.outputs, *([stage.copy_to] if stage.copy_to else [])]:
                produced.append(
                    Path(
                        resolve_variables(raw, variables, where="prepare produced paths")
                    ).resolve()
                )
        # Preserve existing explicit output declarations outside output_root by
        # accounting for those exact paths, not by changing their destinations.
        self.output_paths = [home, output_root, *produced]
        for path in self.output_paths:
            ensure_plain_path(path)
        for stage in config.stages:
            for raw in [*stage.copy_from, *stage.watched_inputs]:
                path = Path(resolve_variables(raw, variables, where="prepare input identity"))
                if not path.is_absolute():
                    path = (config_dir or repo) / path
                if any(path.resolve().is_relative_to(output) for output in produced):
                    continue
                if path.exists():
                    inputs[str(path.resolve())] = path_digest(
                        path, max_bytes=self.limits.fetch_max_bytes
                    )
        # Per-stage max_bytes caps one copy operation; aggregate budgets in
        # config.budgets remain the accounting identity. Excluding max_bytes lets
        # an existing job rerun a stale stage under a tightened cap and report
        # "exceeds" instead of failing closed on plan identity.
        config_dump = config.model_dump()
        for entry in config_dump.get("stages", []):
            if isinstance(entry, dict):
                entry.pop("max_bytes", None)
        # Bind watched/copy input structure (which paths), not their current
        # content digests: changed content reruns as a stale stage under the
        # same cumulative account (planner detects staleness), without resetting
        # spent allowance. Path changes still bind a different identity.
        payload = json.dumps(
            {
                "config": config_dump,
                "copy_inputs": sorted(inputs.keys()),
                "code": path_digest(repo / "src"),
                "lock": path_digest(repo / "uv.lock"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = hashlib.sha256(payload.encode()).hexdigest()
        self.journal = ProgressJournal(self.control / "resources.json", config.id, identity)
        self.journal.bind_roots(
            output=output_root,
            home=home,
            **{f"declared_{i}": path for i, path in enumerate(produced)},
        )
        self.capacity = StorageCapacityManager(
            self.limits.max_subprocess_output_bytes,
            self.limits.max_decompressed_bytes,
            self.limits.max_temp_disk_bytes,
            self.limits.max_output_disk_bytes,
            journal=self.journal,
            extra_limits={
                "input_bytes": self.limits.fetch_max_bytes,
                "network_requests": self.limits.max_network_requests,
            },
        )
        self.capacity.bind_deadline(self.limits.overall_deadline_seconds)
        self.capacity.reconcile_disk("temp", self.spool)

    def check(self) -> None:
        self.capacity.check_deadline()
        # Inspect both output and artifact-store paths; overlapping roots count once.
        roots = self.output_paths
        unique = [
            path
            for path in roots
            if not any(
                path != other and path.resolve().is_relative_to(other.resolve()) for other in roots
            )
        ]
        total, entries = 0, 0
        pending = list(set(unique))
        while pending:
            path = pending.pop()
            ensure_plain_path(path)
            if not path.exists() or path == self.control:
                continue
            entries += 1
            if entries > 10000:
                raise ValueError("preparation tree entry limit exceeded")
            if path.is_dir():
                pending.extend(bounded_children(path))
            else:
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue  # A child's private atomic stage was retired.
        if total > self.limits.max_output_disk_bytes:
            raise ValueError("aggregate preparation output disk limit exceeded")
        with self.journal.transaction() as state:
            state.accounting.occupancy["output"] = total

    def validate_outputs(self, paths: list[Path]) -> None:
        limits = AcquisitionLimits(
            max_records=self.limits.max_records,
            max_decompressed_bytes=self.limits.max_decompressed_bytes,
            max_record_bytes=self.limits.max_record_bytes,
            max_parser_bytes=max(self.limits.max_record_bytes, 32 * 1024**2),
        )
        seen: set[Path] = set()
        pending = list(paths)
        records = 0
        while pending:
            path = pending.pop()
            ensure_plain_path(path)
            path = path.resolve()
            if path in seen or not path.exists():
                continue
            seen.add(path)
            if len(seen) > 10000:
                raise ValueError("prepare output inspection entry limit exceeded")
            if path.is_dir():
                pending.extend(bounded_children(path))
            elif path.name.endswith((".jsonl", ".jsonl.gz", ".parquet")):
                records += inspect_records(path, path.name, limits, self.capacity) or 0
        with self.journal.transaction() as state:
            used = state.accounting.consumed.get("inspected_records", 0)
            if used + records > self.limits.max_records:
                raise ValueError("aggregate preparation record limit exceeded")
            state.accounting.consumed["inspected_records"] = used + records

    def run(self, argv: list[str], root: Path, timeout: float) -> str:
        """Drain both pipes into bounded owned files; never capture_output in RAM."""
        self.check()
        acquisition = self._reserve_acquisition(argv, root)
        self.journal.record_request(self.limits.max_attempts)
        self.spool.mkdir(parents=True, exist_ok=True)
        label = uuid.uuid4().hex
        errors: list[BaseException] = []
        paths = [self.spool / f"{label}.{kind}.log" for kind in ("stdout", "stderr")]
        env = {
            **os.environ,
            "XLM_HOME": str(self.home),
            "TMP": str(self.spool),
            "TEMP": str(self.spool),
            "TMPDIR": str(self.spool),
        }
        process = subprocess.Popen(
            argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        def drain(stream: Any, path: Path) -> None:
            try:
                with path.open("xb") as output:
                    while True:
                        if errors:
                            break
                        # Read without holding shared allowance so an idle pipe
                        # blocked waiting for EOF cannot starve the active pipe.
                        # RAM is bounded to one 8192-byte chunk per worker.
                        chunk = stream.read1(8192)
                        if not chunk:
                            break
                        while chunk:
                            if errors:
                                break
                            remaining_transfer = self.capacity.remaining("transfer")
                            remaining_temp = self.capacity.remaining("temp")
                            allow = min(len(chunk), remaining_transfer, remaining_temp)
                            if allow <= 0:
                                # Shared cap is filled and this chunk proves extra
                                # bytes exist beyond it. Retain nothing more; count
                                # one discarded probe byte per overflowing pipe.
                                self.capacity.record_units(
                                    "discarded_child_probe_bytes", 1, 2 * self.limits.max_attempts
                                )
                                raise ValueError("aggregate child output limit exceeded")
                            head = chunk[:allow]
                            try:
                                token = self.capacity.reserve_transfer(len(head))
                            except (BudgetExhaustedError, DiskCeilingExceededError, ValueError):
                                time.sleep(0.01)
                                continue
                            try:
                                disk = self.capacity.reserve_disk_space(self.spool, len(head))
                            except (BudgetExhaustedError, DiskCeilingExceededError, ValueError):
                                self.capacity.settle("transfer", token, 0)
                                time.sleep(0.01)
                                continue
                            self.capacity.settle("transfer", token, len(head))
                            output.write(head)
                            output.flush()
                            self.capacity.settle("temp", disk, len(head))
                            chunk = chunk[allow:]
                            if chunk:
                                # Remainder in memory already proves overflow beyond
                                # the shared cap; never retain it.
                                self.capacity.record_units(
                                    "discarded_child_probe_bytes", 1, 2 * self.limits.max_attempts
                                )
                                raise ValueError("aggregate child output limit exceeded")
            except BaseException as exc:
                errors.append(exc)
            finally:
                stream.close()

        threads = [
            threading.Thread(target=drain, args=(stream, path), daemon=True)
            for stream, path in zip((process.stdout, process.stderr), paths, strict=True)
        ]
        for thread in threads:
            thread.start()
        start = time.monotonic()
        owned: dict[int, psutil.Process] = {}
        try:
            parent = psutil.Process(process.pid)
            while process.poll() is None or any(thread.is_alive() for thread in threads):
                if errors:
                    raise errors[0]
                if time.monotonic() - start > timeout:
                    raise TimeoutError("preparation stage timeout")
                try:
                    for child in parent.children(recursive=True):
                        owned[child.pid] = child
                except psutil.NoSuchProcess:
                    pass
                self.check()
                time.sleep(0.02)
            if errors:
                raise errors[0]
            self.check()
        finally:
            if process.poll() is None or any(thread.is_alive() for thread in threads):
                for child in owned.values():
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
            process.wait(timeout=10)
            for thread in threads:
                thread.join(timeout=10)
        tail = ""
        for path in paths:
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 1000))
                    tail += stream.read(1000).decode("utf-8", errors="replace")
        if process.returncode:
            raise ValueError(f"command exited {process.returncode}: {tail[-500:]}")
        if acquisition is not None:
            child, before, reservations = acquisition
            child.save()
            if child.state.status != "COMPLETED":
                raise ValueError("nested acquisition did not complete")
            pending = child.state.accounting.reservations
            if any(sum(values.values()) for values in pending.values()):
                # Unknown child outcomes keep their complete parent reservation.
                return tail
            after = {
                "input_bytes": child.state.transferred_bytes,
                "network_requests": child.state.requests_made,
            }
            for resource, token in reservations.items():
                actual = after[resource] - before[resource] if resource in after else 0
                self.capacity.settle(resource, token, actual)
            scratch = child.journal_path.parent.parent
            from xlm.prepare.integrity import bounded_files

            occupied = sum(path.stat().st_size for path in bounded_files(scratch))
            with self.journal.transaction() as state:
                state.nested_storage[str(scratch.resolve())] = occupied
                state.accounting.occupancy["nested_temp"] = sum(state.nested_storage.values())
        return tail

    def _reserve_acquisition(self, argv: list[str], root: Path) -> Any:
        """Reserve a nested fetch's full declared bounds before it can execute."""
        from xlm.data.acquisition.plan import load_acquisition_plan

        command = argv[3:]
        if command[:2] != ["data", "fetch"]:
            if command[:2] == ["data", "probe"] and "--live" in command:
                raise ValueError("run reviewed source discovery separately before preparation")
            return None

        def option(name: str, alias: str, default: Path | None = None) -> Path:
            for flag in (name, alias):
                if flag in command:
                    value = Path(command[command.index(flag) + 1])
                    return value if value.is_absolute() else root / value
            if default is None:
                raise ValueError(f"nested fetch requires explicit {name}")
            return default

        plan = load_acquisition_plan(option("--plan", "-p"))
        scratch = option(
            "--scratch-dir", "--scratch-dir", self.home / "acquisition" / plan.plan_id / "scratch"
        )
        output = option("--output-dir", "-o", self.home / "acquisition" / plan.plan_id / "raw")
        if any(
            not any(path.resolve().is_relative_to(base.resolve()) for base in self.output_paths)
            for path in (scratch, output)
        ):
            raise ValueError("nested acquisition paths escape managed preparation roots")
        child = ProgressJournal(
            scratch / "journals" / f"{plan.plan_id}.progress.json",
            plan.plan_id,
            plan.compute_behavioral_hash(),
        )
        before = {
            "input_bytes": child.state.transferred_bytes,
            "network_requests": child.state.requests_made,
        }
        amounts = {
            "input_bytes": plan.limits.max_transferred_bytes,
            "network_requests": plan.limits.max_requests,
            "temp": plan.limits.max_temp_disk_bytes,
            "output": plan.limits.max_output_disk_bytes + plan.limits.max_temp_disk_bytes,
        }
        reservations: dict[str, str] = {}
        try:
            for resource, amount in amounts.items():
                reservations[resource] = self.capacity.reserve(resource, amount, persistent=True)
        except BaseException:
            for resource, token in reservations.items():
                self.capacity.settle(resource, token, 0)
            raise
        return child, before, reservations
