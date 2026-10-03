"""Content-free compiled-matcher capacity audit for the operator's protected scratch.

Reads the protected index, compiles the private matcher beside the protected root
(``X:/C05-Scratch/...`` in deployment) and prints aggregate metrics only: counts,
bucket quantiles, bytes, RSS and seconds. No token, signature, provenance or path
from inside the index is ever emitted. Ceilings other than RAM, scratch and the
stage deadline are report-only here so the operator can size a reviewed decision.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from xlm.data.exclusion import compact
from xlm.data.exclusion.capacity import FILE_SLACK_BYTES, admit_plan, probe_geometry
from xlm.data.exclusion.compact import CeilingExceeded, CompactExactMatcher
from xlm.data.exclusion.isolation import (
    MARKER_NAME,
    VolumeInspector,
    checkout_root,
    inspect_protected_root,
    os_volume,
    overlaps,
)
from xlm.data.exclusion.policy import C05Error, Resources


def process_tree_rss() -> int:
    process = psutil.Process()
    rss = int(process.memory_info().rss)
    for child in process.children(recursive=True):
        try:
            rss += int(child.memory_info().rss)
        except psutil.NoSuchProcess:
            pass
    return rss


class Monitor:
    """Sampled RAM/scratch ceilings and the stage deadline (sampling can miss a peak)."""

    def __init__(self, resources: Resources, scratch: Path) -> None:
        self.resources, self.scratch = resources, scratch
        self.started = time.monotonic()
        self.peak_rss = process_tree_rss()
        self.peak_scratch = 0
        self._ram = self._disk = 0.0

    def scratch_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.scratch.rglob("*") if p.is_file())

    def __call__(self) -> None:
        now = time.monotonic()
        if now - self.started > self.resources.stage_seconds:
            raise CeilingExceeded("stage_seconds")
        if now - self._ram >= 0.05:
            self._ram = now
            self.peak_rss = max(self.peak_rss, process_tree_rss())
            if self.peak_rss > self.resources.ram_bytes:
                raise CeilingExceeded("ram_bytes", {"peak_rss_bytes": self.peak_rss})
        if now - self._disk >= 0.25:
            self._disk = now
            self.peak_scratch = max(self.peak_scratch, self.scratch_bytes())
            if self.peak_scratch > self.resources.scratch_bytes:
                raise CeilingExceeded("scratch_bytes", {"peak_scratch_bytes": self.peak_scratch})


def protected_scratch(
    index: Path, scratch: Path, inspector: VolumeInspector | None = None
) -> dict[str, Any]:
    """The index must sit in a marked protected root; scratch beside it, same device.

    Mirrors the detached-volume ``c05_scratch`` role: no overlap with the protected
    root or the repository checkout, and the protected root's filesystem device.
    """
    root = next(
        (p for p in index.resolve().parents if (p / MARKER_NAME).is_file()),
        None,
    )
    if root is None:
        raise C05Error("protected audit index is not inside a marked protected root")
    protected = inspect_protected_root(root, inspector)
    if overlaps(scratch, protected.path):
        raise C05Error("matcher scratch overlaps the protected benchmark root")
    if overlaps(scratch, checkout_root()):
        raise C05Error("matcher scratch overlaps the repository checkout")
    if (inspector or os_volume)(scratch) != protected.volume:
        raise C05Error("matcher scratch must be on the protected benchmark volume")
    return {"protected_root_logical_id": protected.logical_id, "same_device": True}


def _self_check(matcher: CompactExactMatcher, samples: int) -> dict[str, Any]:
    """Each sampled pattern, embedded between unknown tokens, must be detected."""
    unique = int(matcher.manifest["counts"]["unique_patterns"])
    chosen = np.unique(np.linspace(0, unique - 1, min(samples, unique)).astype(np.int64))
    filler = "\x00c05-self-check"
    while filler in matcher.vocabulary:
        filler += "x"
    detected = identical = 0
    for pattern in chosen.tolist():
        tokens = matcher.tokens(pattern)
        hit = matcher.match([filler, *tokens, filler])
        detected += hit is not None
        identical += hit == matcher.identity(pattern)
    return {
        "patterns_checked": int(chosen.size),
        "patterns_detected": detected,
        # A sub-pattern ending earlier is the correct historical hit, so this may be lower.
        "identity_equal_to_embedded": identical,
        "unknown_only_document_clean": matcher.match([filler] * 32) is None,
        "passed": detected == chosen.size and matcher.match([filler] * 32) is None,
    }


def audit(
    index: Path,
    resources: Resources,
    scratch: Path,
    *,
    mode: str = "protected",
    backend: str = "compact",
    self_check: int = 1000,
    inspector: VolumeInspector | None = None,
    monitor_factory: Callable[[Resources, Path], Monitor] = Monitor,
) -> dict[str, Any]:
    location = protected_scratch(index, scratch, inspector) if mode == "protected" else {}
    scratch.mkdir(parents=True, exist_ok=True)
    monitor = monitor_factory(resources, scratch)
    index_bytes = index.stat().st_size
    ceilings = {
        name: getattr(resources, name)
        for name in (
            "ram_bytes",
            "scratch_bytes",
            "benchmark_bytes",
            "benchmark_patterns",
            "automaton_nodes",
            "stage_seconds",
        )
    }
    base: dict[str, Any] = {
        "content_free": True,
        "mode": mode,
        "backend": backend,
        "location": location,
        "index_bytes": index_bytes,
        "ceilings": ceilings,
    }
    try:
        if backend == "streaming":
            return {**base, **_streaming(index, resources, monitor)}
        return {**base, **_compact(index, index_bytes, resources, scratch, monitor, self_check)}
    except CeilingExceeded as exc:
        return {
            **base,
            "refused": True,
            "ceiling": exc.ceiling,
            "details": exc.details,
            "peak_rss_bytes": monitor.peak_rss,
            "elapsed_seconds": round(time.monotonic() - monitor.started, 3),
        }


def _streaming(index: Path, resources: Resources, monitor: Monitor) -> dict[str, Any]:
    """Historical automaton, for authored comparison only (refuses at its ceilings)."""
    from xlm.data.exclusion.runner import index_patterns
    from xlm.data.exclusion.streaming import StreamingMatcher

    started = time.monotonic()
    try:
        matcher = StreamingMatcher(
            index_patterns(index, resources.document_bytes),
            max_patterns=resources.benchmark_patterns,
            max_nodes=resources.automaton_nodes,
            check=monitor,
        )
    except CeilingExceeded:
        raise
    except C05Error as exc:
        for phrase, ceiling in (
            ("node ceiling", "automaton_nodes"),
            ("pattern limit", "benchmark_patterns"),
        ):
            if phrase in str(exc):
                raise CeilingExceeded(ceiling, {"peak_rss_bytes": monitor.peak_rss}) from exc
        raise
    monitor()
    return {
        "unique_token_patterns": len(matcher.provenance),
        "logical_trie_nodes": matcher.nodes,
        "compile_seconds": round(time.monotonic() - started, 3),
        "peak_compile_rss_bytes": monitor.peak_rss,
    }


def _compact(
    index: Path,
    index_bytes: int,
    resources: Resources,
    scratch: Path,
    monitor: Monitor,
    self_check: int,
) -> dict[str, Any]:
    from xlm.data.exclusion.runner import file_sha

    directory = scratch / compact.MATCHER_DIR
    staging = scratch / compact.STAGING_DIR
    if directory.exists():
        raise C05Error("audit scratch already holds a compiled matcher; use an empty directory")
    compact.discard_staging(staging)
    index_sha256 = file_sha(index, monitor)
    staging.mkdir()
    started = time.monotonic()
    try:
        manifest = compact.compile_index(
            index,
            staging,
            index_sha256=index_sha256,
            index_bytes=index_bytes,
            max_record=resources.document_bytes,
            max_records=None,
            max_logical_nodes=None,
            check=monitor,
        )
    except BaseException:
        compact.discard_staging(staging)  # Never leave a partial protected copy.
        raise
    compiled_seconds = time.monotonic() - started
    compile_rss = monitor.peak_rss
    os.rename(staging, directory)
    before_open = process_tree_rss()
    started = time.monotonic()
    with CompactExactMatcher(
        directory, index_sha256=index_sha256, index_bytes=index_bytes, check=monitor
    ) as matcher:
        open_seconds = time.monotonic() - started
        open_rss = process_tree_rss() - before_open
        started = time.monotonic()
        checked = _self_check(matcher, self_check) if self_check else {"patterns_checked": 0}
        check_seconds = time.monotonic() - started
    monitor()
    counts = manifest["counts"]
    compiled_bytes = sum(p.stat().st_size for p in directory.iterdir())
    bound = compact.compiled_bound(
        resources.benchmark_bytes, resources.benchmark_patterns, FILE_SLACK_BYTES
    )
    try:
        admission: dict[str, Any] = admit_plan(resources, probe_geometry(scratch))
        scratch_fits, worst = True, admission["worst_case_bytes"]
    except C05Error:
        scratch_fits, worst = False, None
    fits = {
        "ram_bytes": monitor.peak_rss <= resources.ram_bytes,
        "scratch_bytes": scratch_fits and compiled_bytes <= bound,
        "automaton_nodes": counts["logical_trie_nodes"] <= resources.automaton_nodes,
        "benchmark_patterns": counts["index_records"] <= resources.benchmark_patterns,
        "benchmark_bytes": index_bytes <= resources.benchmark_bytes,
        "anchor_bucket": manifest["anchor_statistics"]["max_bucket"] <= compact.MAX_ANCHOR_BUCKET,
    }
    return {
        "index_sha256": index_sha256,
        "index_records": counts["index_records"],
        "unique_token_patterns": counts["unique_patterns"],
        "vocabulary_tokens": counts["vocabulary_tokens"],
        "vocabulary_bytes": counts["vocabulary_bytes"],
        "flattened_pattern_tokens": counts["flattened_tokens"],
        "min_pattern_tokens": counts["min_pattern_tokens"],
        "max_pattern_tokens": counts["max_pattern_tokens"],
        "logical_trie_nodes": counts["logical_trie_nodes"],
        "anchor": {
            "policy": compact.ANCHOR_POLICY,
            "q": compact.ANCHOR_Q,
            "bucket_ceiling": compact.MAX_ANCHOR_BUCKET,
            **manifest["anchor_statistics"],
        },
        "compiled_matcher_bytes": compiled_bytes,
        "compiled_matcher_bound_bytes": bound,
        "worst_case_aggregate_scratch_bytes": worst,
        "peak_compile_rss_bytes": compile_rss,
        "peak_rss_bytes": monitor.peak_rss,
        "matcher_open_rss_delta_bytes": open_rss,
        "bytes_per_unique_pattern": round(compiled_bytes / counts["unique_patterns"], 3),
        "bytes_per_flattened_token": round(compiled_bytes / counts["flattened_tokens"], 3),
        "compile_seconds": round(compiled_seconds, 3),
        "verified_open_seconds": round(open_seconds, 3),
        "self_check": {**checked, "seconds": round(check_seconds, 3)},
        "fits": fits,
        "all_fit": all(fits.values()) and checked.get("passed", True) is not False,
    }
