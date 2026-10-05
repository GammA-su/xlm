"""C05 component forensics FAST path: oracle identity, worker identity, progress, refusals.

One real authored C05 run (the SYNTH seed-URL chain of ``test_c05_component_forensics``)
is analysed by the 858eb9e reference tool (the oracle) and by the fast path. Authored
fixtures only; no real data, protected material or network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
from scripts.c05_authored_pilot import PROMPT

from test_c05_component_forensics import build
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import forensics

REPO = Path(__file__).resolve().parents[1]
SALT_ENV = "XLM_FORENSICS_TEST_SALT"
STAGES = [
    "PLAN VERIFY",
    "INPUT DISCOVERY",
    "DECISION LEDGER",
    "MAJOR COMPONENT",
    "FACT UNITS",
    "BENCHMARK INDEX",
    "CORPUS SCAN",
    "GRAPH BUILD",
    "UNION / COMPONENTS",
    "COUNTERFACTUALS",
    "EDGE ATTRIBUTION",
    "FOCUS ANALYSIS",
    "REPORT VERIFY",
    "COMPLETE",
]


def arguments(chain: dict[str, Any], *extra: str) -> list[str]:
    return [
        "--plan",
        str(chain["plan"]),
        "--read-corpus",
        "--focus-allocation",
        "common_pile_prose/common_pile_prose/project_gutenberg",
        "--focus-allocation",
        "synth_en_explanations/default/-",
        "--benchmark-index",
        str(chain["root"] / "prepared" / "index.jsonl"),
        "--salt-env",
        SALT_ENV,
        *extra,
    ]


def invoke(module: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        cwd=REPO,
        env={**os.environ, SALT_ENV: "authored-test-salt"},
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("forensics-fast") / "root"
    plan = build(root, "chain")
    work = next((root / "scratch").glob("*/decisions.jsonl")).parent
    reference = invoke(
        "scripts.c05_component_forensics_reference", arguments({"plan": plan, "root": root})
    )
    assert reference.returncode == 0
    decisions = [json.loads(x) for x in (work / "decisions.jsonl").read_bytes().splitlines()]
    yield {
        "root": root,
        "plan": plan,
        "work": work,
        "reference": json.loads(reference.stdout),
        "decisions": decisions,
    }


@pytest.fixture(scope="module")
def fast(chain: dict[str, Any]) -> dict[str, Any]:
    done = invoke(
        "scripts.c05_component_forensics", arguments(chain, "--workers", "4", "--no-progress")
    )
    assert done.returncode == 0 and done.stderr == b""
    return {"bytes": done.stdout, "body": json.loads(done.stdout)}


# -- identity with the reference oracle -----------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "staged_union",
        "each_class_alone",
        "single_key_removal_probes",
        "exclusion_counterfactuals",
        "direct_hit_documents",
        "direct_hits_by_allocation",
        "excluded_only_through_propagation",
        "hit_roots_before_bridging",
        "hit_patterns",
        "non_majority_members",
        "dominant_keys",
        "edges_by_class",
        "edges_by_allocation_pair",
        "lineage_key_posting_histogram",
        "documents",
        "bytes",
        "documents_by_allocation",
        "duplicate_groups",
        "distinct_lineage_keys",
        "distinct_seed_url_keys",
        "graph_candidate_edges",
        "reconstruction_complete",
    ],
)
def test_component_fields_equal_the_reference(
    chain: dict[str, Any], fast: dict[str, Any], field: str
) -> None:
    assert fast["body"]["target_component"][field] == chain["reference"]["target_component"][field]


def test_whole_report_equals_the_reference_except_the_corpus_breakdown(
    chain: dict[str, Any], fast: dict[str, Any]
) -> None:
    body, reference = dict(fast["body"]), dict(chain["reference"])
    assert body.pop("ledger_matches_completion") is True
    corpus, old = body.pop("corpus_attribution"), reference.pop("corpus_attribution")
    assert body == reference  # focus allocations, decisions, hits, component: all identical
    for key in ("recomputed_url_keys_found_in_units", "recomputed_url_keys_missing_from_units"):
        assert corpus[key] == old[key]
    assert corpus["allocation"] == '["synth_en_explanations","default",null]'
    synth = [r for r in chain["decisions"] if r["component"] == "synth_en_explanations"]
    assert sum(sum(v.values()) for v in corpus["url_metadata_presence_by_state"].values()) == len(
        synth
    )
    assert corpus["files_verified"] == corpus["files_read"] >= 1
    assert body["target_component"]["reconstruction_complete"] is True


@pytest.mark.parametrize("workers", ["1", "2", "8", "16"])
def test_worker_count_never_changes_a_report_byte(
    chain: dict[str, Any], fast: dict[str, Any], workers: str
) -> None:
    done = invoke(
        "scripts.c05_component_forensics", arguments(chain, "--workers", workers, "--no-progress")
    )
    assert done.returncode == 0 and done.stdout == fast["bytes"]


# -- progress contract ------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "jsonl"])
def test_progress_is_stderr_only_and_stdout_is_exactly_the_report(
    chain: dict[str, Any], fast: dict[str, Any], fmt: str
) -> None:
    done = invoke(
        "scripts.c05_component_forensics",
        arguments(
            chain, "--workers", "4", "--progress-format", fmt, "--progress-interval", "0.001"
        ),
    )
    assert done.returncode == 0
    assert done.stdout == fast["bytes"]  # progress settings never change report bytes
    lines = done.stderr.decode("utf-8").splitlines()
    if fmt == "jsonl":
        events = [json.loads(line) for line in lines]
        stages = [e["stage"] for e in events if e["event"] in ("stage", "complete")]
        ledger = [
            e for e in events if e.get("stage") == "DECISION LEDGER" and e["event"] == "finish"
        ]
        assert ledger[0]["done"] == ledger[0]["total"] == len(chain["decisions"])
        assert {"bytes_done", "bytes_total", "workers"} <= set(ledger[0]["fields"])
        assert all("rss" in e["telemetry"] for e in events if e["telemetry"])
    else:
        assert lines and all(line.startswith("[FORENSICS] ") for line in lines)
        stages = [
            line.removeprefix("[FORENSICS] ").split(" | ")[0]
            for line in lines
            if " | started" in line or line.startswith("[FORENSICS] COMPLETE")
        ]
        assert any("ETA " in line for line in lines) and any("RSS " in line for line in lines)
    assert stages == STAGES
    assert_content_free(done.stderr.decode("utf-8"), chain)


def test_no_progress_leaves_stderr_empty(fast: dict[str, Any]) -> None:
    # Text-mode stdout (CRLF on Windows, like the reference); the fixture checked stderr.
    assert fast["bytes"].rstrip().endswith(b"}") and json.loads(fast["bytes"])["kind"]


def assert_content_free(text: str, chain: dict[str, Any]) -> None:
    assert "http" not in text and "wikipedia" not in text and "Seed_" not in text
    assert PROMPT not in text and "Generated wrapper" not in text
    assert not re.search(r"[0-9a-f]{32,}", text)
    assert str(chain["root"]) not in text and "authored-test-salt" not in text
    for row in chain["decisions"]:
        assert row["doc_id"] not in text


# -- refusals and cleanup -------------------------------------------------------------------


def refusal(done: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    assert done.returncode == 1 and done.stdout == b""  # never a complete-looking report
    lines = done.stderr.decode("utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["refused"] is True
    return record


def test_malformed_and_short_ledgers_refuse(chain: dict[str, Any], tmp_path: Path) -> None:
    raw = (chain["work"] / "decisions.jsonl").read_bytes().splitlines(keepends=True)
    cases = {
        "garbage": (raw[:5] + [b"{not json\n"] + raw[5:], "decision ledger record is not JSON"),
        "schema": (raw[:5] + [b'{"doc_id":"x"}\n'] + raw[5:], "decision ledger record schema"),
        "short": (raw[:-1], "decision ledger row count differs from the C05 plan"),
    }
    for name, (lines, reason) in cases.items():
        path = tmp_path / f"{name}.jsonl"
        path.write_bytes(b"".join(lines))
        done = invoke(
            "scripts.c05_component_forensics",
            arguments(chain, "--decisions", str(path), "--workers", "4", "--no-progress"),
        )
        record = refusal(done)
        assert record["reason"] == reason and record["stage"] == "DECISION LEDGER"
        assert_content_free(done.stderr.decode("utf-8"), chain)


def test_corrupt_fact_unit_refuses(chain: dict[str, Any], tmp_path: Path) -> None:
    facts = tmp_path / "facts"
    shutil.copytree(chain["work"] / "facts", facts)
    unit = sorted(facts.glob("*.unit"))[0]
    data = bytearray(unit.read_bytes())
    data[0] ^= 0xFF  # magic
    unit.write_bytes(bytes(data))
    done = invoke(
        "scripts.c05_component_forensics",
        arguments(chain, "--facts", str(facts), "--workers", "2", "--no-progress"),
    )
    record = refusal(done)
    assert record["stage"] == "FACT UNITS" and record["error_type"] == "C05Error"


def test_corrupt_protected_index_refuses(chain: dict[str, Any], tmp_path: Path) -> None:
    index = tmp_path / "index.jsonl"
    original = (chain["root"] / "prepared" / "index.jsonl").read_bytes()
    for name, damage, reason in (
        ("json", b"\x00\x01garbage\n", "protected index record is not JSON"),
        ("schema", b'{"tokens": 7}\n', "protected index record schema"),
    ):
        index.write_bytes(original + damage)
        args = arguments(chain, "--workers", "4", "--no-progress")
        args[args.index("--benchmark-index") + 1] = str(index)
        done = invoke("scripts.c05_component_forensics", args)
        record = refusal(done)
        assert record["reason"] == reason, name
        assert record["stage"] == "BENCHMARK INDEX"
        assert "garbage" not in done.stderr.decode("utf-8")


def test_changed_corpus_file_refuses(chain: dict[str, Any], tmp_path: Path) -> None:
    plan = canonical.loads_bytes_strict(chain["plan"].read_bytes())
    data = tmp_path / "data"
    shutil.copytree(plan["data_root"], data)
    synth = next(f for f in plan["files"] if f["component"] == "synth_en_explanations")
    target = data / synth["path"]
    raw = target.read_bytes()
    # Same length and still valid JSON: only the content hash can notice the change.
    changed = raw.replace(b'"text":"w', b'"text":"x', 1)
    assert changed != raw and len(changed) == len(raw)
    target.write_bytes(changed)
    moved = tmp_path / "plan.json"
    canonical.write_canonical_json(moved, {**plan, "data_root": str(data)})
    args = arguments(chain, "--decisions", str(chain["work"] / "decisions.jsonl"))
    args[args.index("--plan") + 1] = str(moved)
    args += ["--facts", str(chain["work"] / "facts"), "--workers", "2", "--no-progress"]
    record = refusal(invoke("scripts.c05_component_forensics", args))
    assert record["reason"] == "cleaned corpus file differs from the C05 plan"
    assert record["stage"] == "CORPUS SCAN"


def test_interrupt_and_worker_failure_reap_every_child(
    chain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(SALT_ENV, "authored-test-salt")
    original = forensics.read_ledger

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)  # the spawned pool is busy, then the operator presses Ctrl-C
        raise KeyboardInterrupt

    monkeypatch.setattr(forensics, "read_ledger", interrupted)
    assert forensics.main(arguments(chain, "--workers", "4", "--no-progress")) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error_type"] == "KeyboardInterrupt"
    assert psutil.Process().children(recursive=True) == []
    monkeypatch.setattr(forensics, "read_ledger", original)

    raw = (chain["work"] / "decisions.jsonl").read_bytes()
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(raw + b"[1,2,3]\n")  # a worker raises while parsing its block
    args = arguments(chain, "--decisions", str(broken), "--workers", "4", "--no-progress")
    assert forensics.main(args) == 1
    captured = capsys.readouterr()
    assert (
        captured.out == "" and json.loads(captured.err)["reason"] == "decision ledger record schema"
    )
    assert psutil.Process().children(recursive=True) == []
