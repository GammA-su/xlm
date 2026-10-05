"""Read-only forensics of one C05 exclusion component, on real authored C05 runs.

Each fixture runs the authored C05 flow over a generated Mix-01-shaped corpus whose
SYNTH rows carry seed URLs, so the actual production grouping builds the graph:

* about 3,000 SYNTH rows with unique text: thousands of independent duplicate groups;
* 60 seed articles: rows of one seed share ``query_seed_url`` (written in several URL
  spellings that ``canonical_url`` must merge);
* ``chain``: every seventh row also names the next seed as ``additional_seed_url``, a
  transitive bridge chain over all 60 seed families;
* ``hub``: every seventh row names one domain-level ``additional_seed_url``, a single
  shared link joining every seed family;
* some SYNTH rows carry no URL (independent families that survive);
* the planted benchmark row of every allocation is a direct hit; SYNTH's own sits in
  seed family 0, and every other member of the component is excluded only through
  propagation;
* the flow's planted exact cross-allocation copies put a FinePDFs and a Wiki-Rewrite
  document into the SYNTH component through duplicate edges (the cross-source bridge).

No real data, benchmark material or network.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripts import c05_synthetic_flow as flow_module
from scripts.c05_authored_pilot import KEY, PROMPT
from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05

from xlm.data.evidence_v2 import canonical

SEEDS = 60
SYNTH = '["synth_en_explanations","default",null]'
GUTENBERG = '["common_pile_prose","common_pile_prose","project_gutenberg"]'
SPELLINGS = (
    "https://en.wikipedia.org/wiki/Seed_{s}",
    "http://www.en.wikipedia.org/wiki/Seed_{s}/#History",
    "https://en.wikipedia.org/wiki/Seed_{s}?utm_source=x",
)


def seed_metadata(scheme: str, number: int, text: str) -> dict[str, Any]:
    if PROMPT in text:
        return {"query_seed_url": SPELLINGS[0].format(s=0), "exercise": "mcq"}
    if number % 37 == 5:
        return {"exercise": "memorization"}  # no seed URL: an independent family
    seed = number % SEEDS
    metadata: dict[str, Any] = {
        "query_seed_url": SPELLINGS[number % 3].format(s=seed),
        "exercise": "rag",
    }
    if number % 7 == 0:
        metadata["additional_seed_url"] = (
            SPELLINGS[0].format(s=(seed + 1) % SEEDS)
            if scheme == "chain"
            else "https://en.wikipedia.org/"
        )
    return metadata


def build(root: Path, scheme: str) -> Path:
    monkey = pytest.MonkeyPatch()
    monkey.setenv(KEY_ENV, KEY)
    original = flow_module.doc

    def doc(number: int, source: str, text: str, metadata: dict[str, Any] | None = None) -> Any:
        if source == "synth":
            metadata = seed_metadata(scheme, number, text)
        return original(number, source, text, metadata)

    monkey.setattr(flow_module, "doc", doc)
    monkey.setattr(
        flow_module,
        "ALLOCATIONS",
        [
            (c, s, v, u, 120_000 if c == "synth_en_explanations" else q)
            for c, s, v, u, q in flow_module.ALLOCATIONS
        ],
    )
    try:
        paths = prepare(root)
        plan_path = decide_and_plan(paths)
        run_c05(paths, plan_path)
    finally:
        monkey.undo()
    return plan_path


@pytest.fixture(scope="module", params=["chain", "hub"])
def run(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp(f"forensics-{request.param}") / "root"
    plan_path = build(root, request.param)
    command = [
        sys.executable,
        "-m",
        "scripts.c05_component_forensics",
        "--plan",
        str(plan_path),
        "--read-corpus",
        "--focus-allocation",
        "common_pile_prose/common_pile_prose/project_gutenberg",
        "--benchmark-index",
        str(root / "prepared" / "index.jsonl"),
    ]
    done = subprocess.run(
        command,
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
    )
    text = done.stdout.decode("utf-8")
    plan = canonical.loads_bytes_strict(plan_path.read_bytes())
    decisions = [
        json.loads(line)
        for line in next((root / "scratch").rglob("decisions.jsonl")).read_bytes().splitlines()
    ]
    yield {
        "scheme": request.param,
        "body": json.loads(text),
        "text": text,
        "plan": plan,
        "decisions": decisions,
        "root": root,
    }


def ground_truth(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    excluded: dict[str, int] = {}
    for row in decisions:
        if row["decision"] == "excluded":
            excluded[row["lineage_group"]] = excluded.get(row["lineage_group"], 0) + 1
    target = max(excluded, key=lambda k: excluded[k])
    members = [r for r in decisions if r["lineage_group"] == target]
    return {"target": target, "members": members}


def test_component_is_reconstructed_exactly_from_c05_facts(run: dict[str, Any]) -> None:
    truth = ground_truth(run["decisions"])
    component = run["body"]["target_component"]
    assert component["documents"] == len(truth["members"]) > 2000
    assert component["bytes"] == sum(r["bytes"] for r in truth["members"])
    assert component["reconstruction_complete"] is True
    assert component["duplicate_groups"] == len({r["duplicate_group"] for r in truth["members"]})
    assert component["duplicate_groups"] > 2000  # thousands of independent groups
    assert component["documents_by_allocation"][SYNTH] > 2000
    by_allocation = component["documents_by_allocation"]
    assert set(by_allocation) - {SYNTH}  # cross-source members exist


def test_bridge_class_and_propagation_are_attributed(run: dict[str, Any]) -> None:
    component = run["body"]["target_component"]
    n = component["documents"]
    stages = {s["after_class"]: s for s in component["staged_union"]}
    seed = stages["url:query_seed_url"]
    # Same-seed rows form one family per seed article: the URL spellings merge, seeds don't.
    assert seed["components"] >= SEEDS
    assert seed["largest_component"] < n / 10
    bridge = stages["url:additional_seed_url"] if "url:additional_seed_url" in stages else None
    mixed = [c for c in stages if "additional_seed_url" in c]
    assert mixed, "the additional_seed_url class must exist"
    final = component["staged_union"][-1]
    assert final["components"] == 1 and final["would_be_excluded"] == n
    # Without the additional-seed links, exclusion would stop at the hit's seed family.
    assert seed["would_be_excluded"] < n / 10
    alone = component["each_class_alone"]
    assert all(v["largest_component"] < n for v in alone.values())
    assert component["direct_hit_documents"] >= 1
    assert component["excluded_only_through_propagation"] == n - component["direct_hit_documents"]
    assert component["hit_roots_before_bridging"] >= 1
    assert component["direct_hits_by_allocation"].get(SYNTH, 0) >= 1
    if run["scheme"] == "hub":
        top = component["dominant_keys"][0]
        assert top["shape"]["domain_level"] is True and set(top["fields"]) == {
            "additional_seed_url"
        }
        probe = component["single_key_removal_probes"][0]
        assert probe["removed_fingerprint"] == top["fingerprint"]
        assert probe["components"] >= SEEDS  # one shared key holds the component together
    else:
        assert not any(
            k["shape"]["domain_level"] for k in component["dominant_keys"] if "shape" in k
        )
        assert bridge is None or bridge["merges"] >= SEEDS - 1


def test_cross_source_members_attach_through_duplicate_edges(run: dict[str, Any]) -> None:
    attachments = run["body"]["target_component"]["non_majority_members"]
    assert attachments
    for member in attachments:
        if member["direct_hit"]:
            continue  # another allocation's planted benchmark row (near-duplicate of SYNTH's)
        assert set(member["edges_by_class"]) == {"duplicate"}
        assert set(member["partner_allocations"]) == {SYNTH}


def test_corpus_attribution_and_survivors(run: dict[str, Any]) -> None:
    corpus = run["body"]["corpus_attribution"]
    presence = corpus["url_metadata_presence_by_state"]
    # URL-less rows join only through duplicate edges (planted copies), never by URL.
    assert presence["target_component"].get("no_url_metadata", 0) <= 3
    assert presence["kept"]["no_url_metadata"] > 50  # the URL-less rows survive
    assert corpus["recomputed_url_keys_missing_from_units"] == 0
    assert corpus["recomputed_url_keys_found_in_units"] > 2000
    assert corpus["exercise_label_by_state"]["kept"]["memorization"] > 50
    assert run["body"]["target_component"]["distinct_seed_url_keys"] == SEEDS


def test_focus_allocation_direct_hits(run: dict[str, Any]) -> None:
    gutenberg = run["body"]["focus_allocations"][GUTENBERG]
    rows = [r for r in run["decisions"] if r["upstream_component"] == "project_gutenberg"]
    assert gutenberg["documents"] == len(rows)
    excluded = sum(1 for r in rows if r["decision"] == "excluded")
    assert gutenberg["decisions"].get("excluded", 0) == excluded
    assert (
        gutenberg["excluded_with_direct_hit"] + gutenberg["excluded_only_through_propagation"]
        == excluded
    )
    patterns = gutenberg["hit_patterns"]
    assert patterns["direct_hit_documents"] >= 1
    assert all("token_length" in p and p["kinds"] for p in patterns["top_patterns"])


def test_report_is_content_free(run: dict[str, Any]) -> None:
    text = run["text"]
    assert "wikipedia.org" not in text and "Seed_" not in text and "http" not in text
    assert PROMPT not in text and "Generated wrapper" not in text
    for row in run["decisions"]:
        assert row["doc_id"] not in text
        assert row["lineage_group"] not in text and row["duplicate_group"] not in text
    fingerprints = re.findall(r'"fingerprint": "([0-9a-f]+)"', text)
    fingerprints += re.findall(r'"removed_fingerprint": "([0-9a-f]+)"', text)
    assert fingerprints and all(len(f) == 16 for f in fingerprints)
    scrubbed = re.sub(r'"(removed_)?fingerprint": "[0-9a-f]+"', "", text)
    scrubbed = scrubbed.replace(run["body"]["plan_digest"], "")
    scrubbed = scrubbed.replace(str(run["body"]["plan_code_commit"]), "")
    assert not re.search(r"[0-9a-f]{32,}", scrubbed)
    assert str(run["root"]) not in text


def test_derivation_counterfactuals_bound_the_transitive_exclusion(run: dict[str, Any]) -> None:
    component = run["body"]["target_component"]
    n = component["documents"]
    counterfactual = component["exclusion_counterfactuals"]
    assert counterfactual["current_transitive_family"] == n
    assert 1 <= counterfactual["duplicate_and_parent_only"] <= counterfactual["same_seed_family"]
    assert (
        counterfactual["same_seed_family"]
        <= counterfactual["same_seed_family_plus_one_hop_additional_seed"]
    )
    assert counterfactual["same_seed_family_plus_one_hop_additional_seed"] < n / 10
    if run["scheme"] == "chain":
        # Rows naming the hit's seed as an additional seed are excluded one hop away.
        assert (
            counterfactual["same_seed_family_plus_one_hop_additional_seed"]
            > counterfactual["same_seed_family"]
        )
