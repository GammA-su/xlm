"""End-to-end authored C05 runs: legacy (c05-production-v2) vs policy v2 (c05-production-v3).

The same generated Mix-01-shaped corpus and benchmark run through the actual operator
flow twice (plan, authorize, interrupted run, resume, verify, proof, C06 fit, exact
count, selection):

* legacy: c05-matcher-v4 index, every pattern triggers, one transitive lineage family:
  the 60 seed families chained by ``additional_seed_url`` collapse into one giant family;
* v2: the same index with c05-trigger-floors-v1 and query-seed derivation families.

Every per-allocation decision is checked against the independent naive oracle of
``c05_policy_support`` (``current x current_transitive`` and
``prompt8 x query_seed_family``). Authored fixtures only.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from c05_policy_support import COMMON, GOOD, LONG_Q, SYNTH, build_run, oracle
from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.fitscan import MEMBERSHIP_KEYS, MEMBERSHIP_KEYS_V3
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import (
    MatcherPolicyV4,
    ProductionPolicy,
    ProductionPolicyV3,
    TriggerPolicy,
)

PRESSBOOKS = '["common_pile_prose","common_pile_prose","pressbooks"]'
GUTENBERG = '["common_pile_prose","common_pile_prose","project_gutenberg"]'
LIBRETEXTS = '["common_pile_prose","common_pile_prose","libretexts"]'
# Authored benchmark items without any active trigger under c05-trigger-floors-v1:
# the 5-token PIQA goal with yes/no and the 5-token HellaSwag context with yes/no.
REVIEWED_UNCOVERED = 2
SEED = re.compile(r"Seed_(\d+)")


def legacy_policy() -> ProductionPolicy:
    return ProductionPolicy(
        matcher=MatcherPolicyV4(), diagnostic_bytes=4096, quick_bytes=0, audit_bytes=0
    )


def v2_policy(reviewed: int = REVIEWED_UNCOVERED) -> ProductionPolicyV3:
    return ProductionPolicyV3(
        trigger=TriggerPolicy(reviewed_items_without_active_trigger=reviewed),
        diagnostic_bytes=4096,
        quick_bytes=0,
        audit_bytes=0,
    )


def load_run(built: dict[str, Any]) -> dict[str, Any]:
    root = built["root"]
    decisions = [
        json.loads(x)
        for x in next((root / "scratch").rglob("decisions.jsonl")).read_bytes().splitlines()
    ]
    plan = canonical.loads_bytes_strict(built["plan"].read_bytes())
    seeds: dict[str, int | None] = {}
    for item in plan["files"]:
        if item["source_id"] != "synth":
            continue
        for line in (Path(plan["data_root"]) / item["path"]).read_bytes().splitlines():
            doc = CanonicalDocument(**json.loads(line))
            found = SEED.search(str(doc.source_metadata.get("query_seed_url", "")))
            seeds[doc.doc_id] = int(found.group(1)) if found else None
    return {**built, "decisions": decisions, "seeds": seeds, "oracle": oracle(root, built["plan"])}


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("legacy") / "root"
    yield load_run(build_run(root, matcher=MatcherPolicyV4(), policy=legacy_policy()))


@pytest.fixture(scope="module")
def v2(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("policy-v2") / "root"
    built = build_run(root, matcher=MatcherPolicyV4(), policy=v2_policy(), require_deficit=False)
    yield load_run(built)


def per_allocation(decisions: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for row in decisions:
        key = canonical.canonical_bytes(
            [row["component"], row["view"], row["upstream_component"]]
        ).decode()
        table[key][row["decision"]] += 1
    return table


def assert_matches_oracle(run: dict[str, Any], cell: str) -> None:
    expected = run["oracle"][cell]
    got = per_allocation(run["decisions"])
    for name, row in expected.items():
        for decision in ("kept", "duplicate", "excluded"):
            assert got[name][decision] == row[decision], (cell, name, decision)


# -- the legacy policy reproduces the giant transitive SYNTH family ---------------------------


def test_legacy_policy_builds_the_giant_transitive_synth_family(legacy: dict[str, Any]) -> None:
    assert_matches_oracle(legacy, "current|current_transitive")
    rows = legacy["decisions"]
    assert all(set(r) == MEMBERSHIP_KEYS for r in rows)
    synth_excluded = Counter(
        r["lineage_group"]
        for r in rows
        if r["source_id"] == "synth" and r["decision"] == "excluded"
    )
    size = synth_excluded.most_common(1)[0][1]
    assert size > 2900  # every chained seed family in one exclusion family
    completion = legacy["completion"]["payload"]
    assert completion["kind"] == "c05_completion_v2" and "trigger" not in completion


# -- policy v2 ------------------------------------------------------------------------------


def test_v2_decisions_equal_the_audited_counterfactual(v2: dict[str, Any]) -> None:
    assert_matches_oracle(v2, "prompt8|query_seed_family")


def test_v2_contracts_and_completion_bind_the_trigger(v2: dict[str, Any]) -> None:
    rows = v2["decisions"]
    assert all(set(r) == MEMBERSHIP_KEYS_V3 for r in rows)
    body = v2["completion"]["payload"]
    assert body["kind"] == "c05_completion_v3"
    assert body["output_contract"] == "c05_membership_v3"
    trigger = body["trigger"]
    assert trigger["policy_digest"] == v2_policy().trigger.identity()
    assert trigger["items_without_active_trigger"] == REVIEWED_UNCOVERED
    assert trigger["benchmark_items"] == 6 and trigger["active_items"] == 4
    assert body["exclusion_lineage"] == "query-seed-derivation-family-v1"
    assert body["split_lineage"] == "known-lineage-v3"
    plan = canonical.loads_bytes_strict(v2["plan"].read_bytes())
    assert plan["output_contract"] == "c05_membership_v3"
    assert plan["policy"]["version"] == "c05-production-v3"


def seed_of(run: dict[str, Any], row: dict[str, Any]) -> int | None:
    return run["seeds"].get(row["doc_id"])


def test_v2_exclusion_follows_the_query_seed_family_only(v2: dict[str, Any]) -> None:
    rows = [r for r in v2["decisions"] if r["source_id"] == "synth"]
    excluded_seeds = Counter(seed_of(v2, r) for r in rows if r["decision"] == "excluded")
    by_seed: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_seed[seed_of(v2, r)].append(r)
    # Seed 10 holds a true copy of the long ARC item: its whole derivation family goes.
    assert all(r["decision"] == "excluded" for r in by_seed[10])
    # Seed 5 holds only the 4-token HellaSwag fragment: no active trigger, nothing goes.
    assert all(r["decision"] != "excluded" for r in by_seed[5])
    # Seeds 9 and 11 are linked to seed 10 by additional_seed_url (A -> B -> C chain):
    # the link never propagates exclusion.
    assert all(r["decision"] != "excluded" for r in by_seed[9] + by_seed[11])
    assert set(excluded_seeds) <= {0, 10}
    # Same query seed => one exclusion family; different seeds => different families.
    groups = {seed: {r["exclusion_group"] for r in members} for seed, members in by_seed.items()}
    assert len(groups[10]) == 1 and groups[10] != groups[11]


def test_v2_split_grouping_stays_broader_and_never_changes_exclusion(
    v2: dict[str, Any],
) -> None:
    rows = v2["decisions"]
    synth = [r for r in rows if r["source_id"] == "synth" and seed_of(v2, r) is not None]
    split_groups = Counter(r["split_group"] for r in synth)
    exclusion_groups = {r["exclusion_group"] for r in synth}
    assert split_groups.most_common(1)[0][1] > 2900  # the transitive split family is kept
    assert len(exclusion_groups) >= 60  # while contamination families stay per seed
    # A split family with any excluded member is never diagnostic/audit.
    tainted = {r["split_group"] for r in rows if r["decision"] == "excluded"}
    assert not [r for r in rows if r["split"] != "train" and r["split_group"] in tainted]
    # Exclusion is exactly the oracle's query-seed-family exclusion, independent of the
    # (broader) split grouping: checked cell-by-cell in the oracle test above.


def test_v2_keeps_short_blimp_and_duplicate_propagation(v2: dict[str, Any]) -> None:
    rows = v2["decisions"]
    libretexts = [r for r in rows if r["upstream_component"] == "libretexts"]
    pressbooks = [r for r in rows if r["upstream_component"] == "pressbooks"]
    # The single short BLiMP sentence and the BLiMP pair are still exclusion triggers.
    assert sum(r["decision"] == "excluded" for r in libretexts) >= 1
    pair = next(r for r in pressbooks if r["decision"] == "excluded" and r["row"] == 1)
    # Its exact cross-source copy (next allocation, Gutenberg) is excluded with it.
    copies = [r for r in rows if r["duplicate_group"] == pair["duplicate_group"]]
    assert {r["upstream_component"] for r in copies} >= {"pressbooks", "project_gutenberg"}
    assert all(r["decision"] == "excluded" for r in copies)


def test_v2_recovers_the_short_prompt_false_positives(
    legacy: dict[str, Any], v2: dict[str, Any]
) -> None:
    old, new = per_allocation(legacy["decisions"]), per_allocation(v2["decisions"])
    assert new[SYNTH]["excluded"] < 0.05 * old[SYNTH]["excluded"]
    assert new[GUTENBERG]["excluded"] < old[GUTENBERG]["excluded"]
    assert sum(c["kept"] for c in new.values()) > sum(c["kept"] for c in old.values())
    for secret in (COMMON, LONG_Q, GOOD):  # nothing protected leaks into decisions
        assert secret not in json.dumps(v2["decisions"])


# -- downstream binding -------------------------------------------------------------------------


def test_downstream_runs_on_v2_and_refuses_legacy_artifacts(
    legacy: dict[str, Any], v2: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.c05_authored_pilot import KEY

    monkeypatch.setenv(KEY_ENV, KEY)
    root = v2["root"]
    counts = canonical.loads_bytes_strict((root / "counts/counts.json").read_bytes())["payload"]
    assert counts["completion_digest"] == v2["completion"]["digest"]
    assert (root / "selection/selection.json").is_file()
    signing = ["--issuer", ISSUER, "--key-env", KEY_ENV]
    # The legacy tokenizer (fitted on the legacy completion) is refused for the v2 proof.
    stale = operator(
        [
            "count-tokens",
            "--c05-proof",
            str(v2["proof"]),
            "--tokenizer",
            str(legacy["tokenizer"]),
            "--scratch",
            str(tmp_path / "scratch"),
            "--output",
            str(tmp_path / "counts"),
            *signing,
        ]
    )
    assert stale != 0 and not (tmp_path / "counts").exists()
    # Legacy counts are refused with the v2 proof.
    mixed = operator(
        [
            "select",
            "--c05-proof",
            str(v2["proof"]),
            "--tokenizer",
            str(v2["tokenizer"]),
            "--scratch",
            str(tmp_path / "scratch-select"),
            "--counts",
            str(legacy["root"] / "counts"),
            "--quotas",
            str(v2["quotas"]),
            "--ifm-split",
            str(root / "ifm-split.json"),
            "--deficit-report",
            str(tmp_path / "deficit.json"),
            "--output",
            str(tmp_path / "selection"),
            *signing,
        ]
    )
    assert mixed != 0 and not (tmp_path / "selection").exists()


def test_v2_run_refuses_a_wrong_reviewed_coverage(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="reviewed count"):
        build_run(
            tmp_path / "root",
            matcher=MatcherPolicyV4(),
            policy=v2_policy(reviewed=0),
            require_deficit=False,
        )
    # Refused before any corpus row: no fact unit was written.
    assert not list((tmp_path / "root" / "scratch").rglob("*.unit"))


# -- benchmark-recall acceptance ------------------------------------------------------------


def test_trigger_matcher_reproduces_the_audited_prompt8_recall(
    legacy: dict[str, Any], tmp_path: Path
) -> None:
    import os
    import subprocess
    import sys

    root = legacy["root"]
    repo = Path(__file__).resolve().parents[1]
    audit = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.c05_policy_counterfactual",
            "audit",
            "--plan",
            str(legacy["plan"]),
            "--benchmark-index",
            str(root / "prepared/index.jsonl"),
            "--benchmark-receipt",
            str(root / "prepared/benchmark-preparation.receipt.json"),
            "--benchmark-material",
            str(root / "material"),
            "--workers",
            "1",
            "--no-progress",
        ],
        capture_output=True,
        cwd=repo,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert audit.returncode == 0, audit.stderr.decode(errors="replace")[-800:]
    audited = json.loads(audit.stdout)["injected_copy_recall"]["by_task_split_and_form"]
    policy = tmp_path / "policy-value.json"
    canonical.write_canonical_json(policy, v2_policy().model_dump(mode="json"))
    arguments = [
        "trigger-recall-local",
        "--receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
        "--policy",
        str(policy),
        "--index",
        str(root / "prepared/index.jsonl"),
        "--material-root",
        str(root / "material"),
        "--mode",
        "authored",
    ]
    done = subprocess.run(
        [sys.executable, "-m", "xlm.data.exclusion.operator", *arguments,
         "--scratch", str(tmp_path / "recall"),
         "--expect-items-without-active-trigger", str(REVIEWED_UNCOVERED),
         "--expect-blimp-single-sentence-detected", "1"],
        capture_output=True, cwd=repo, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-800:]
    report = json.loads(done.stdout)
    assert report["accepted"] is True and report["content_free"] is True
    for group, forms in audited.items():
        for form, entry in forms.items():
            got = report["by_task_split_and_form"][group][form]
            assert got["items"] == entry["items"]
            # The production compile detects exactly what the audited candidate did.
            assert got["detected"] == entry["detected"]["prompt8"], (group, form)
    # BLiMP single-sentence protection is kept (it is what floor8 would lose).
    blimp = report["by_task_split_and_form"]["blimp/authored-split"]["single_sentence"]
    assert (
        blimp["detected"]
        == audited["blimp/authored-split"]["single_sentence"]["detected"]["current"]
    )
    # A different expectation is a blocking refusal (exit 2).
    blocked = subprocess.run(
        [sys.executable, "-m", "xlm.data.exclusion.operator", *arguments,
         "--scratch", str(tmp_path / "recall-2"), "--expect-whole-item-detected", "6"],
        capture_output=True, cwd=repo, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert blocked.returncode == 2
    assert json.loads(blocked.stdout)["expectation_mismatches"]["whole_item_detected"] == {
        "expected": 6,
        "observed": report["whole_item"]["detected"],
    }
    for secret in (COMMON, LONG_Q, GOOD):
        assert secret.casefold() not in (done.stdout + done.stderr).decode().casefold()


def test_operator_proof_accepts_the_v2_completion(
    v2: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.c05_authored_pilot import KEY

    monkeypatch.setenv(KEY_ENV, KEY)
    root = v2["root"]
    output = tmp_path / "policy-v2.proof.json"
    code = operator(
        [
            "proof",
            "--plan",
            str(v2["plan"]),
            "--manifest",
            str(root / "manifest.json"),
            "--scratch",
            str(tmp_path / "lookup"),
            "--output",
            str(output),
            "--trust",
            str(root / "trust.json"),
            "--signer",
            ISSUER,
            "--signer-key-env",
            KEY_ENV,
        ]
    )
    assert code == 0 and output.is_file()
    proof = canonical.loads_bytes_strict(output.read_bytes())
    proof = proof.get("payload", proof)
    assert proof["completion_digest"] == v2["completion"]["digest"]
