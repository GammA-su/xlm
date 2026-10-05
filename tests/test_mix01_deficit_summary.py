"""Read-only Mix-01 deficit summary over the authored 17-allocation chain (no real data).

The authored chain is not cleaned, so its manifest serves as both original and cleaned
manifest (cleaning survival 1.0). Every number must reconcile with the artifacts, and
nothing but aggregates and artifact identities may reach stdout.
"""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER

from test_c06_tokenizer_fit import c05, key  # noqa: F401 (fixtures)
from test_count_tokens_fast import tok  # noqa: F401 (fixture)
from test_select_fast import chain, fast_outcome, resigned, rows_edit  # noqa: F401
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed
from xlm.data.exclusion.inputs import read_metadata

ARTIFACT_KEYS = {
    "original_manifest_digest",
    "cleaned_manifest_digest",
    "plan_digest_in_proof",
    "completion_digest",
    "completion_plan_digest",
    "completion_input_manifest_digest",
    "plan_input_manifest_digest",
    "counts_digest",
    "counts_completion_digest",
    "counts_tokenizer_fingerprint",
    "report_sha256",
    "report_counts_digest",
    "quota_sha256",
    "decisions_sha256",
    "inventory_digest",
}


def summarize(*args: str) -> tuple[dict[str, Any], str]:
    done = subprocess.run(
        [sys.executable, "-m", "scripts.mix01_deficit_summary", *args],
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
    )
    text = done.stdout.decode("utf-8")
    return json.loads(text), text


def no_identity_leaks(body: dict[str, Any], text: str, chain: dict[str, Any]) -> None:
    def scrub(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return None if key in ARTIFACT_KEYS else value

    rest = json.dumps(scrub(body))
    assert not re.search(r"[0-9a-f]{32,}", rest)  # no per-record id or hash
    for row in chain["membership"]:
        assert row["doc_id"] not in text
    for row in chain["decisions"]:
        assert row["duplicate_group"] not in rest and row["lineage_group"] not in rest
    assert "Context:" not in text and "documents.jsonl" not in text


def test_summary_reconciles_deficit_ledger_and_inventory(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    def scarce(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**r, "valid_targets": 1} if r["allocation"][0] == "synth_en_explanations" else r
            for r in rows
        ]

    counts = resigned(chain["counts"], tmp_path / "counts", rows_edit(scarce))
    # Keep the signed per-allocation totals consistent with the edited rows, as
    # count-tokens writes them (resigned() only rebinds hash, size and documents).
    body = read_metadata(counts / "counts.json", digested=False)["payload"]
    totals: dict[str, dict[str, int]] = {}
    for raw in (counts / "counts.jsonl").read_bytes().splitlines():
        row = canonical.loads_bytes_strict(raw)
        bucket = totals.setdefault(
            canonical.canonical_bytes(row["allocation"]).decode(),
            {"documents": 0, "valid_targets": 0},
        )
        bucket["documents"] += 1
        bucket["valid_targets"] += row["valid_targets"]
    body["allocations"] = dict(sorted(totals.items()))
    (counts / "counts.json").unlink()
    canonical.write_canonical_json(counts / "counts.json", signed(body, ISSUER, KEY.encode()))
    outcome = fast_outcome(c05, tok, counts, tmp_path / "f")
    assert outcome[0] == "deficit"
    (tmp_path / "deficit.json").write_bytes(outcome[1])
    manifest = c05["root"] / "manifest.json"
    original = read_metadata(manifest, digested=False)
    common_files = sorted(
        {f["source_file"] for f in original["files"] if f["source_key"] == "common_pile"}
    )
    names = [
        *common_files,
        "news/news.chunk.9.jsonl.gz",
        "oercommons/a.jsonl.gz",
        "pressbooks/b.jsonl.gz",
    ]
    inventory = {
        "inventory_digest": "0" * 64,
        "files": [
            {"file": n, "order_key": str(i), "size_bytes": 100 + i} for i, n in enumerate(names)
        ],
    }
    canonical.write_canonical_json(tmp_path / "common_pile.inventory.json", inventory)
    decisions = next((c05["root"] / "scratch").rglob("decisions.jsonl"))
    body, text = summarize(
        "--original-manifest",
        str(manifest),
        "--cleaned-manifest",
        str(manifest),
        "--proof",
        str(c05["proof"]),
        "--counts",
        str(counts),
        "--deficit-report",
        str(tmp_path / "deficit.json"),
        "--inventory-dir",
        str(tmp_path),
        "--decisions",
        str(decisions),
    )
    no_identity_leaks(body, text, chain)
    artifacts = body["artifacts"]
    assert (
        artifacts["completion_digest_matches_proof"]
        and artifacts["original_manifest_self_digest_ok"]
    )
    assert artifacts["report_kind"] == "c05_selection_deficit_v1"
    allocations = body["allocations"]
    assert len(allocations) == 17
    completion = read_metadata(
        Path(read_metadata(c05["proof"], digested=False)["completion"]) / "completion.json",
        digested=False,
    )["payload"]
    counts_body = read_metadata(counts / "counts.json", digested=False)["payload"]
    deficit = json.loads(outcome[1])
    for name, entry in allocations.items():
        assert entry["acquired"]["documents"] == entry["cleaned"]["documents"]
        assert entry["ratios"]["cleaning_document_survival"] == 1.0
        c = entry["c05"]
        assert c["kept"] + c["excluded"] + c["duplicate"] == entry["acquired"]["documents"]
        assert c["kept"] == completion["allocations"][name]["kept"]
        assert entry["exact_valid_targets"] == counts_body["allocations"][name]["valid_targets"]
        assert entry["deficit"] == deficit["allocations"][name]["deficit"]
    synth = allocations['["synth_en_explanations","default",null]']
    assert (
        synth["status"] == "DEFICIT"
        and synth["exact_valid_targets"] == synth["c05"]["kept_train_documents"]
    )
    assert body["totals"]["deficit"] == sum(a["deficit"] for a in deficit["allocations"].values())
    ledger = body["ledger"]
    for name, decided in ledger["decisions_by_allocation"].items():
        c = allocations[name]["c05"]
        assert decided.get("kept", 0) == c["kept"] and decided.get("duplicate", 0) == c["duplicate"]
        assert decided.get("excluded", 0) == c["excluded"]
    assert ledger["excluded_families"] >= 1 and ledger["largest_excluded_families"]
    # The planted cross-allocation copies survive in another allocation.
    assert any(
        any(k.startswith("other allocation") for k in v)
        for v in ledger["duplicate_groups_by_survivor"].values()
    )
    common = body["sources"]["common_pile"]["inventory"]
    assert common["acquired"]["files"] == len(common_files)
    assert common["remaining"]["files"] == 3
    assert common["remaining_excluding_reserved_tail"]["files"] == 1  # last two ranks reserved
    assert set(common["by_upstream"]) >= {"news", "oercommons", "pressbooks"}
    assert body["sources"]["synth"]["inventory"] is None  # no inventory given


def test_summary_accepts_a_selection_when_there_is_no_deficit(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / "selection.json").write_bytes(chain["reference"]["selection.json"])
    manifest = c05["root"] / "manifest.json"
    body, text = summarize(
        "--original-manifest",
        str(manifest),
        "--cleaned-manifest",
        str(manifest),
        "--proof",
        str(c05["proof"]),
        "--counts",
        str(chain["counts"]),
        "--selection",
        str(tmp_path / "selection.json"),
    )
    no_identity_leaks(body, text, chain)
    assert body["totals"]["deficit"] == 0 and "ledger" not in body
    assert all(a["status"] == "EXACT" for a in body["allocations"].values())
    assert body["artifacts"]["report_kind"] == "c05_selected_pool_v1"
    for entry in body["allocations"].values():
        r = entry["ratios"]
        assert r["targets_per_c05_kept_train_byte"] == pytest.approx(
            entry["exact_valid_targets"] / entry["c05"]["kept_train_bytes"], abs=1e-6
        )
