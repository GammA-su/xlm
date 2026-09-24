"""Optional pytest evidence plugin; direct pytest works without this plugin.

Use ``-p pytest_evidence --evidence-json PATH`` with scripts on PYTHONPATH.
Records collection, phases and sampled process-tree RSS without changing selection.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--evidence-json", type=Path, help="Atomic local test evidence JSON")


def pytest_configure(config: pytest.Config) -> None:
    path = config.getoption("--evidence-json")
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(Evidence(path, config), "xlm-test-evidence")


class Evidence:
    def __init__(self, path: Path, config: pytest.Config) -> None:
        self.path = path
        self.config = config
        self.start = time.perf_counter()
        self.collected: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.worker_collections: dict[str, list[str]] = {}
        self.peak = 0
        self.peak_processes = 0
        self.cpu_samples: dict[tuple[int, float], float] = {}
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self.sample, daemon=True)
        self.monitor.start()

    def sample(self) -> None:
        parent = psutil.Process()
        while not self.stop.is_set():
            total = 0
            count = 0
            for process in [parent, *parent.children(recursive=True)]:
                try:
                    total += process.memory_info().rss
                    cpu = process.cpu_times()
                    key = (process.pid, process.create_time())
                    self.cpu_samples[key] = max(
                        self.cpu_samples.get(key, 0.0), cpu.user + cpu.system
                    )
                    count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self.peak = max(self.peak, total)
            self.peak_processes = max(self.peak_processes, count)
            self.stop.wait(0.5)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        self.collected = [
            {
                "nodeid": item.nodeid,
                "markers": sorted({mark.name for mark in item.iter_markers()}),
                "fixtures": list(getattr(item, "fixturenames", [])),
                "location": list(item.location),
            }
            for item in items
        ]

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collection_seconds = time.perf_counter() - self.start
        self.selected = [item.nodeid for item in session.items]

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: Any, ids: list[str]) -> None:
        self.worker_collections[node.gateway.id] = list(ids)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": report.duration,
                "worker": getattr(report, "worker_id", None),
                "properties": list(report.user_properties),
            }
        )

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self.stop.set()
        self.monitor.join(timeout=2)
        value = {
            "argv": self.config.invocation_params.args,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "worker_collections": self.worker_collections,
            "loaded_heavy_modules": [
                n for n in ("torch", "pyarrow", "lm_eval", "datasets") if n in sys.modules
            ],
            "exit_code": int(exitstatus),
            "wall_seconds": time.perf_counter() - self.start,
            "collection_seconds": getattr(self, "collection_seconds", None),
            "peak_tree_rss_bytes": self.peak,
            "rss_sample_seconds": 0.5,
            "sampled_cpu_seconds_lower_bound": sum(self.cpu_samples.values()),
            "observed_unique_processes": len(self.cpu_samples),
            "peak_observed_processes": self.peak_processes,
            "collected": self.collected,
            "selected": getattr(self, "selected", []),
            "reports": self.reports,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
