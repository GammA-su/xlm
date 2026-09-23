"""Collect small P29C evidence from bounded local authored runs; no new benchmarks."""

from __future__ import annotations

import hashlib
import io
import json
import pstats
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_cleaning_v2 import digest_files  # noqa: E402
from verify_cleaning_v2 import compare_cleaning  # noqa: E402

from xlm.data.datasets.shards import load_manifest, verify_manifest  # noqa: E402

SOURCE = ROOT / "artifacts/perf"
DEST = Path(__file__).parent


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(name: str, value: Any) -> None:
    text = value if isinstance(value, str) else json.dumps(value, indent=2) + "\n"
    temporary = DEST / (name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(DEST / name)


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return (
        "\n".join(
            ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
            + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
        )
        + "\n"
    )


def main() -> None:
    reports = {}
    for path in sorted(SOURCE.glob("p29c-*/report.json")):
        reports[path.parent.name] = read(path)
        write(path.parent.name + ".json", reports[path.parent.name])
    for name in ("candidates", "char-candidate", "stage-oracle", "exact-code", "exact-parallel"):
        write("p29c-" + name + ".json", read(SOURCE / ("p29c-" + name + ".json")))

    exact = []
    for name in reports:
        if "accepted_sha256" not in reports[name]:
            continue
        # The initial capped full-input diagnostic is correctly marked partial;
        # the actual 10k prefix is complete. Never erase that scientific field.
        if name == "p29c-profile-before-10k":
            assert reports[name]["scientific_summary"]["is_partial_sample"] is True
            assert (
                reports[name]["accepted_sha256"]
                == reports["p29c-clean-before-10k"]["accepted_sha256"]
            )
            continue
        reference = (
            "p29c-heavy-static"
            if "heavy" in name
            else "p29c-clean-before-10k"
            if "10k" in name or "sharded" in name
            else "p29c-before-w1"
        )
        exact.append(compare_cleaning(SOURCE / reference, SOURCE / name))
    manifests = []
    for name in ("p29c-sharded-w1", "p29c-sharded-w4"):
        root = SOURCE / name / "cleaned"
        manifest = verify_manifest(root, load_manifest(root / "clean-manifest.json"))
        with (SOURCE / "p29c-clean-before-10k/cleaned/documents.jsonl").open("rb") as baseline:
            for entry in manifest.shards:
                digest = hashlib.sha256()
                size = 0
                endpoints = []
                for index in range(entry.doc_count):
                    line = next(baseline)
                    digest.update(line)
                    size += len(line)
                    if index in (0, entry.doc_count - 1):
                        endpoints.append(json.loads(line)["doc_id"])
                assert digest.hexdigest() == entry.sha256 and size == entry.byte_count
                assert endpoints == [entry.first_doc_id, entry.last_doc_id]
            assert baseline.read(1) == b""
        assert (
            digest_files([root / s.path for s in manifest.shards])
            == reports["p29c-clean-before-10k"]["accepted_sha256"]
        )
        value = manifest.to_dict()
        del value["producer"]["workers"]
        manifests.append(value)
    assert manifests[0] == manifests[1]
    assert (
        reports["p29c-fixture"]["fingerprint"]
        == read(DEST.parent / "P29B/p29b-fixture.json")["fingerprint"]
    )
    write("exactness.json", {"cleaner_comparisons": exact, "verified_shard_manifest": manifests[0]})

    output = [
        "# P29C measured results\n",
        "Authored fixtures only. Seconds unless indicated. Raw JSON is adjacent.\n",
    ]
    output += ["## Cleaner worker sweep\n"]
    rows: list[list[Any]] = []
    for mode in ("before", "after"):
        for workers in (1, 2, 4, 6, 8):
            r = reports[f"p29c-{mode}-w{workers}"]
            m, t = r["measurement"], r["throughput"]
            cpu = sum(w["cpu_seconds"] for w in r["worker_telemetry"])
            total_cpu = m["parent_cpu_seconds"] + (cpu if workers > 1 else 0)
            rows.append(
                [
                    mode,
                    workers,
                    f"{m['wall_seconds']:.3f}",
                    f"{t['input_documents'] / m['wall_seconds']:.1f}",
                    f"{t['input_bytes'] / 2**20 / m['wall_seconds']:.3f}",
                    f"{m['peak_tree_rss_bytes'] / 2**20:.1f}",
                    f"{cpu:.3f}",
                    f"{total_cpu:.3f}",
                ]
            )
    output += [
        table(
            [
                "Code",
                "Workers",
                "Wall s",
                "Docs/s",
                "Text MiB/s",
                "Tree RSS MiB",
                "Worker-body CPU s",
                "Parent + worker CPU s*",
            ],
            rows,
        ),
        "*One-worker body CPU is already included in parent CPU. "
        "Child bootstrap CPU is unmeasured.\n",
    ]
    rows = []
    for name in (
        "p29c-after-w8",
        "p29c-dynamic-w8",
        "p29c-shard-1",
        "p29c-shard-16",
        "p29c-heavy-static",
        "p29c-heavy-dynamic",
        "p29c-heavy-repeat-dynamic",
        "p29c-heavy-repeat-static",
    ):
        r = reports[name]
        busy: dict[int, float] = defaultdict(float)
        cpu = residual = 0.0
        starts = []
        for w in r["worker_telemetry"]:
            busy[w["pid"]] += w["wall_seconds"]
            cpu += w["cpu_seconds"]
            residual += w["wall_seconds"] - w["cpu_seconds"]
            starts.append(w["dispatch_to_start_seconds"])
        m = r["measurement"]
        rows.append(
            [
                name.removeprefix("p29c-"),
                r["shard_mib"],
                f"{m['wall_seconds']:.3f}",
                f"{m['peak_tree_rss_bytes'] / 2**20:.1f}",
                f"{cpu:.3f}",
                f"{min(busy.values()):.2f}–{max(busy.values()):.2f}",
                f"{residual:.2f}",
                f"{min(starts):.2f}–{max(starts):.2f}",
            ]
        )
    output += [
        "## Shards, dispatch and uneven work\n",
        table(
            [
                "Run",
                "Shard MiB",
                "Wall s",
                "RSS MiB",
                "Worker CPU s",
                "Per-process busy s range",
                "Sum non-CPU residual s",
                "Dispatch-to-start range s",
            ],
            rows,
        ),
        "Busy time sums task bodies. Residual is wall minus CPU, not OS wait. "
        "Dispatch time includes planning/imports/spawn and, for dynamic tasks, "
        "queueing since run start. Pure startup and OS idle were not isolated.\n",
    ]
    output += ["## Unprofiled stage telemetry, one worker / 100k\n"]
    a, b = [reports[f"p29c-{mode}-w1"]["throughput"] for mode in ("before", "after")]
    rows = [
        [key, f"{a['stage_seconds'][key]:.3f}", f"{b['stage_seconds'][key]:.3f}"]
        for key in a["stage_seconds"]
    ]
    rows += [
        [key, f"{a[key]:.3f}", f"{b[key]:.3f}"]
        for key in (
            "parse_seconds",
            "serialization_seconds",
            "accepted_write_seconds",
            "quarantine_seconds",
            "fsync_seconds",
        )
    ]
    output += [
        table(["Stage", "Before", "After"], rows),
        "Filter timers do not include all post-timer bookkeeping; small values are "
        "clock-quantized. This is attribution, not a complete additive budget.\n",
    ]

    output += ["## Long documents: median of three filter calls, milliseconds\n"]
    long_rows = reports["p29c-long"]["rows"]
    rows = []
    for kind in ("clean", "repetitive", "noisy"):
        for size in sorted({r["bytes"] for r in long_rows if r["kind"] == kind}):
            row = [kind, size]
            for filt in ("language", "repetition", "pii"):
                pair = [
                    next(
                        r
                        for r in long_rows
                        if (r["kind"], r["bytes"], r["filter"], r["mode"])
                        == (kind, size, filt, mode)
                    )
                    for mode in ("before", "after")
                ]
                assert pair[0]["result_digest"] == pair[1]["result_digest"]
                row.append(
                    " → ".join(
                        f"{statistics.median(t['wall'] for t in r['trials']) * 1000:.3f}"
                        for r in pair
                    )
                )
            rows.append(row)
    output += [
        table(
            [
                "Kind",
                "UTF-8 bytes",
                "Language before → after ms",
                "Repetition before → after ms",
                "PII before → after ms",
            ],
            rows,
        )
    ]
    output += ["## Pipeline (fixture generation excluded)\n"]
    names = ("p29c-before-100k", "p29c-final-code-100k", "p29c-final-parallel-100k")
    rows = []
    for stage in reports[names[0]]["stages"][1:]:
        rows.append(
            [stage["stage"]]
            + [
                format(
                    next(
                        s["wall_seconds"]
                        for s in reports[n]["stages"]
                        if s["stage"] == stage["stage"]
                    ),
                    ".3f",
                )
                for n in names
            ]
        )
    rows.append(["TOTAL"] + [f"{reports[n]['pipeline_wall_seconds']:.3f}" for n in names])
    rows.append(
        ["Peak tree RSS MiB"]
        + [
            f"{max(s['peak_tree_rss_bytes'] for s in reports[n]['stages']) / 2**20:.1f}"
            for n in names
        ]
    )
    output += [
        table(["Stage", "Fresh P29B / clean w1", "P29C / clean w1", "P29C / clean w8"], rows),
        "All token stages use eight workers. The pipeline loader is the same 16-step "
        "dummy consumer; the historical P29B 50-step mixture-loader probe (5.062 s) "
        "is a separate benchmark and is not included here.\n",
    ]
    write("RESULTS.md", "\n".join(output))
    for name in ("p29c-profile-10k", "p29c-profile-before-100k", "p29c-profile-after-10k"):
        stream = io.StringIO()
        paths = sorted((SOURCE / name / "workers").glob("*.pstats"))
        stats = pstats.Stats(*map(str, paths), stream=stream)
        stats.strip_dirs().sort_stats("tottime").print_stats(45)
        stats.sort_stats("cumulative").print_stats(35)
        stats.print_stats("length_noise")
        stats.print_callers("method 'search'")
        write(name + ".txt", stream.getvalue().rstrip() + "\n")
    retained = sum(
        p.stat().st_size
        for entry in SOURCE.glob("p29c-*")
        for p in ([entry] if entry.is_file() else entry.rglob("*"))
        if p.is_file()
    )
    write(
        "storage.json",
        {
            "aggregate_retained_bytes": retained,
            "scope": "all artifacts/perf/p29c-* files, including inputs and temporary evidence",
            "pipeline_sampled_peaks": {
                name: max(s["sampled_peak_artifact_bytes"] for s in reports[name]["stages"])
                for name in names
            },
            "limits": (
                "per-job sampled guardrails, not OS reservations; "
                "single-worker scratch is not sampled"
            ),
        },
    )
    evidence = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DEST.iterdir())
        if p.is_file() and p.suffix in (".json", ".txt") and p.name != "inventory.json"
    }
    write("inventory.json", evidence)
    print(
        f"Collected {len(reports)} reports; {len(exact)} exact cleaner comparisons; "
        "two verified manifests."
    )


if __name__ == "__main__":
    main()
