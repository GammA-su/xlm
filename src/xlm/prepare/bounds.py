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
from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.records import inspect_records
from xlm.prepare.config import PrepareBudgets, PrepareConfig


class PrepareBounds:
    def __init__(self, config: PrepareConfig, output_root: Path, home: Path) -> None:
        self.limits = PrepareBudgets.model_validate(config.budgets)
        self.output_root, self.home = output_root, home
        self.control = output_root / ".accounting"
        self.spool = self.control / "logs"
        ensure_plain_path(output_root)
        ensure_plain_path(home)
        payload = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(payload.encode()).hexdigest()
        self.journal = ProgressJournal(self.control / "resources.json", config.id, identity)
        self.journal.bind_roots(output=output_root, home=home)
        self.capacity = StorageCapacityManager(self.limits.max_subprocess_output_bytes,
            self.limits.max_decompressed_bytes, self.limits.max_temp_disk_bytes,
            self.limits.max_output_disk_bytes, journal=self.journal)
        self.capacity.bind_deadline(self.limits.overall_deadline_seconds)

    def check(self) -> None:
        self.capacity.check_deadline()
        self.capacity.reconcile_disk("temp", self.spool)
        # Inspect both output and artifact-store paths; overlapping roots count once.
        roots = [self.home, self.output_root]
        unique = [path for path in roots if not any(path != other and path.resolve().is_relative_to(other.resolve()) for other in roots)]
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
        limits = AcquisitionLimits(max_records=self.limits.max_records,
            max_decompressed_bytes=self.limits.max_decompressed_bytes,
            max_record_bytes=self.limits.max_record_bytes,
            max_parser_bytes=max(self.limits.max_record_bytes, 32 * 1024**2))
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
        self.journal.record_request(self.limits.max_attempts)
        self.spool.mkdir(parents=True, exist_ok=True)
        label = uuid.uuid4().hex
        errors: list[BaseException] = []
        paths = [self.spool / f"{label}.{kind}.log" for kind in ("stdout", "stderr")]
        env = {**os.environ, "XLM_HOME": str(self.home), "TMP": str(self.spool), "TEMP": str(self.spool), "TMPDIR": str(self.spool)}
        process = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def drain(stream: Any, path: Path) -> None:
            try:
                with path.open("xb") as output:
                    while True:
                        amount = min(8192, self.capacity.remaining("transfer"), self.capacity.remaining("temp"))
                        if amount <= 0:
                            # One-byte probe distinguishes EOF; never retained beyond the output cap.
                            if stream.read(1):
                                raise ValueError("aggregate child output limit exceeded")
                            break
                        token = self.capacity.reserve_transfer(amount)
                        disk = self.capacity.reserve_disk_space(self.spool, amount)
                        chunk = stream.read1(amount)
                        self.capacity.settle("transfer", token, len(chunk))
                        if not chunk:
                            self.capacity.settle("temp", disk, 0)
                            break
                        output.write(chunk)
                        output.flush()
                        self.capacity.settle("temp", disk, len(chunk))
            except BaseException as exc:
                errors.append(exc)
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(stream, path), daemon=True)
                   for stream, path in zip((process.stdout, process.stderr), paths, strict=True)]
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
        return tail
