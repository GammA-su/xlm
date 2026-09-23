"""Collect local P29B evidence; run after the documented sequential benchmarks."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import platform
import pstats
import shutil
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    root = Path("artifacts/perf")
    evidence = Path(__file__).resolve().parent
    for path in sorted(root.glob("p29b*/report.json")):
        shutil.copyfile(path, evidence / f"{path.parent.name}.json")
    for path in sorted(root.glob("p29b-*.json")):
        shutil.copyfile(path, evidence / path.name)

    def read(name: str) -> dict:
        return json.loads((evidence / f"p29b-{name}.json").read_text(encoding="utf-8"))

    names = ["tokens.bin", "offsets.jsonl", "shard_counters.json", "shard_manifest.json"]
    baseline = root / "p29b-scale-w1"
    hashes = {
        str(path.relative_to(baseline)): digest(path)
        for name in names
        for path in sorted(baseline.glob(f"shard-*/{name}"))
    }
    checked = {}
    for variant in (
        "scale-w2",
        "scale-w4",
        "scale-w8",
        "batch-w1",
        "batch-w8",
        "batch-w1-t8",
        "batch-w4-t2",
    ):
        target = root / f"p29b-{variant}"
        actual = {
            str(path.relative_to(target)): digest(path)
            for name in names
            for path in sorted(target.glob(f"shard-*/{name}"))
        }
        assert actual == hashes, variant
        checked[variant] = len(actual)
    direct = {}
    for name in names:
        before = digest(root / "p29b-before-100k/tokens" / name)
        assert before == digest(root / "p29b-direct-batch-100k/tokens" / name), name
        direct[name] = before
    rebuilt = {}
    for name in ("tokenizer.json", "tokenizer_manifest.json"):
        before = digest(root / "p29b-fixture/tokenizer" / name)
        assert before == digest(root / "p29b-fixture-final/tokenizer" / name), name
        rebuilt[name] = before
    batch_digests = {
        row["digest"]
        for name in ("mixture-before", "mixture-after", "mixture-final")
        for row in read(name)["mixture"]
    }
    assert len(batch_digests) == 1
    exact = {
        "matrix_reference_hashes": hashes,
        "matrix_files_verified_per_variant": checked,
        "direct_native_batch_hashes": direct,
        "refitted_32768_tokenizer_hashes": rebuilt,
        "mixture_full_batch_cursor_digest": batch_digests.pop(),
        "exit_status": 0,
    }
    (evidence / "exactness.json").write_text(json.dumps(exact, indent=2), encoding="utf-8")
    profiles = {
        "writer-10k": root / "p29b-probe-before/writer.pstats",
        "writer-100k": root / "p29b-probe-100k/writer.pstats",
        "mixture-before": root / "p29b-mixture-before-profile.pstats",
        "cleaning-p29": root / "after-profile/profile.pstats",
    }
    for name, path in profiles.items():
        stream = io.StringIO()
        stats = pstats.Stats(str(path), stream=stream).strip_dirs().sort_stats("tottime")
        stats.print_stats(65)
        stats.print_stats("fsync|manifest|write|hash|encode|language|repet|pii")
        (evidence / f"profile-{name}.txt").write_text(
            stream.getvalue().rstrip() + "\n", encoding="utf-8"
        )

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    changed = subprocess.check_output(
        ["git", "diff", "44c19b8", "--name-only", "--", "*.py"], text=True
    ).splitlines()
    env = {
        "head": head,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "torch",
                "tokenizers",
                "pyarrow",
                "psutil",
                "pytest",
                "pytest-xdist",
                "ruff",
                "mypy",
            )
        },
        "source_sha256": {name: digest(Path(name)) for name in changed},
        "pin_sha256": {
            name: digest(Path(name)) for name in ("pyproject.toml", "uv.lock", ".python-version")
        },
        "retained_p29b_bytes": sum(
            p.stat().st_size
            for d in root.glob("p29b*")
            for p in ([d] if d.is_file() else d.rglob("*"))
            if p.is_file()
        ),
        "retained_all_artifact_bytes": sum(
            p.stat().st_size for p in Path("artifacts").rglob("*") if p.is_file()
        ),
        "free_disk_bytes": shutil.disk_usage(root).free,
    }
    (evidence / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    commits = subprocess.check_output(
        ["git", "log", "--reverse", "--format=%h %s", "--name-only", "44c19b8..HEAD"], text=True
    )
    (evidence / "COMMITS.txt").write_text(commits, encoding="utf-8")

    lines = [
        "# P29B measured results",
        "",
        "All seconds are observed single-run fixture results; "
        "no confidence interval or production claim.",
        "",
    ]
    for size in ("10k", "100k"):
        before, after = (read(f"matched-{mode}-{size}") for mode in ("before", "after"))
        lines += [
            f"## Matched {size}",
            "",
            "| Stage | Before s | After s | Before RSS MiB | After RSS MiB | "
            "Parent fsync before/after |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for a, b in zip(before["stages"], after["stages"], strict=True):
            lines.append(
                f"| {a['stage']} | {a['wall_seconds']:.3f} | {b['wall_seconds']:.3f} | "
                f"{a['peak_tree_rss_bytes'] / 2**20:.1f} | "
                f"{b['peak_tree_rss_bytes'] / 2**20:.1f} | "
                f"{a['parent_fsync_calls']}/{b['parent_fsync_calls']} |"
            )
        a, b = before["pipeline_wall_seconds"], after["pipeline_wall_seconds"]
        lines += [
            "",
            f"Pipeline **{a:.3f} -> {b:.3f} s**, {a / b:.3f}x; "
            f"{abs(1 - b / a) * 100:.2f}% {'less' if b < a else 'more'} wall time.",
            f"Input documents/s: {before['documents'] / a:.1f} -> {after['documents'] / b:.1f}.",
            "",
        ]
        for mode, report in (("before", before), ("after", after)):
            row = next(r for r in report["stages"] if r["stage"] == "tokenization_shards")
            manifest = row["manifest"]
            lines.append(
                f"{mode}: {manifest['num_tokens']:,} framed tokens, "
                f"{manifest['num_documents']:,} accepted docs; token stage "
                f"{manifest['num_tokens'] / row['wall_seconds']:,.0f} tokens/s, "
                f"{manifest['num_documents'] / row['wall_seconds']:.1f} docs/s. "
                f"Parent CPU total "
                f"{sum(r['parent_cpu_seconds'] for r in report['stages'][1:]):.3f} s; "
                f"final bytes {report['stages'][-1]['artifact_bytes_after_stage']:,}; "
                f"sampled disk peak "
                f"{max(r['sampled_peak_artifact_bytes'] for r in report['stages']):,} bytes."
            )
        lines += [
            "",
            "Parent counters exclude children; RSS sums process working sets "
            "and may count shared pages repeatedly.",
            "",
        ]
    lines += [
        "## Shard-only worker/batch matrix",
        "",
        "Batch experiments use 128 documents. Native threads default off unless labeled.",
        "",
        "| Variant | Wall s | Worker CPU s | RSS MiB | Mean init s | "
        "Tokens/s | Docs/s | Output MiB/s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in (
        "scale-w1",
        "scale-w2",
        "scale-w4",
        "scale-w8",
        "batch-w1",
        "batch-w8",
        "batch-w1-t8",
        "batch-w4-t2",
    ):
        report = read(variant)
        row = report["stages"][0]
        workers = report["workers_detail"]
        lines.append(
            f"| {variant} | {row['wall_seconds']:.3f} | "
            f"{sum(w['cpu_seconds'] for w in workers):.3f} | "
            f"{row['peak_tree_rss_bytes'] / 2**20:.1f} | "
            f"{sum(w['init_seconds'] for w in workers) / len(workers):.3f} | "
            f"{row['tokens_per_second']:,.0f} | {row['docs_per_second']:.1f} | "
            f"{row['output_mib_per_second']:.2f} |"
        )
    lines += [
        "",
        "These timings exclude final single-shard assembly and input sharding; "
        "use the integrated table for end-to-end claims.",
        "",
        "## Final mixture loader",
        "",
        "| Mode | 50 steps s | Steps/s | Admission s | RSS MiB |",
        "|---|---:|---:|---:|---:|",
    ]
    for phase in ("before", "final"):
        for row in read(f"mixture-{phase}")["mixture"]:
            lines.append(
                f"| {phase}, cache={row['max_open_shards']} | {row['wall_seconds']:.3f} | "
                f"{row['steps_per_second']:.3f} | {row['init_seconds']:.3f} | "
                f"{row['rss_bytes'] / 2**20:.1f} |"
            )
    lines += [
        "",
        "Batch and cursor hashing excluded from timed work; every digest matched.",
        "",
        "## Retained disk",
        "",
        f"P29B retained bytes: {env['retained_p29b_bytes']:,}; "
        f"all artifacts: {env['retained_all_artifact_bytes']:,}; "
        f"disk free at collection: {env['free_disk_bytes']:,} bytes. "
        "This includes diagnostic repetitions; no corpus is committed.",
        "",
    ]
    (evidence / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "exit": 0,
                "matrix_variants": len(checked),
                "files_per_variant": len(hashes),
                "environment": env,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
