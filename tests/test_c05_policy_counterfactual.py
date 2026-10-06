"""C05 contamination-policy audit on a real authored C05 run (stage 1 and stage 2).

Checks: exact reproduction of production; every matcher x lineage cell against an
independent naive oracle; recall audits; content-free output; worker-count identity;
refusals; the token projection under the current tokenizer. Authored fixtures only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scripts.c05_authored_pilot import PROMPT

from c05_policy_support import BAD, COMMON, GOOD, LONG_Q, SYNTH, build_run, oracle
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import candidates as cand
from xlm.data.exclusion import supply
from xlm.data.exclusion.selection import load_tokenizer

REPO = Path(__file__).resolve().parents[1]
SALT_ENV = "XLM_POLICY_AUDIT_TEST_SALT"
GUTENBERG = '["common_pile_prose","common_pile_prose","project_gutenberg"]'
PRESSBOOKS = '["common_pile_prose","common_pile_prose","pressbooks"]'
LIBRETEXTS = '["common_pile_prose","common_pile_prose","libretexts"]'
STAGES = [
    "PLAN VERIFY",
    "INPUT DISCOVERY",
    "GROUP VERIFY",
    "DECISION LEDGER",
    "FACT UNITS",
    "BENCHMARK INDEX",
    "MATCHER IDENTITIES",
    "CORPUS RESCAN",
    "LINEAGE",
    "COUNTERFACTUAL MATRIX",
    "PATTERN CENSUS",
    "INJECTED-COPY RECALL",
    "REPORT VERIFY",
    "COMPLETE",
]


def invoke(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.c05_policy_counterfactual", *args],
        capture_output=True,
        cwd=REPO,
        env={**os.environ, SALT_ENV: "authored-policy-salt"},
        stdin=subprocess.DEVNULL,
        check=False,
    )


def audit_args(run: dict[str, Any], *extra: str) -> list[str]:
    root = run["root"]
    return [
        "audit",
        "--plan",
        str(run["plan"]),
        "--benchmark-index",
        str(root / "prepared/index.jsonl"),
        "--benchmark-receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
        "--salt-env",
        SALT_ENV,
        *extra,
    ]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    root = tmp_path_factory.mktemp("policy") / "root"
    built = build_run(root)
    state = root / "policy-state.npz"
    done = invoke(
        *audit_args(
            built,
            "--benchmark-material",
            str(root / "material"),
            "--state-out",
            str(state),
            "--workers",
            "4",
            "--progress-interval",
            "0.01",
        )
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-2000:]
    yield {
        **built,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "body": json.loads(done.stdout),
        "state": state,
        "oracle": oracle(root, built["plan"]),
    }


def test_current_matcher_and_lineage_reproduce_production_exactly(run: dict[str, Any]) -> None:
    body = run["body"]
    assert body["content_free"] is True
    reproduction = body["reproduction"]
    assert reproduction["decisions_equal_ledger"] is True
    assert reproduction["splits_equal_ledger"] is True
    assert reproduction["family_splits_equal_group"] is True
    assert reproduction["rebuilt_lineage_families_equal_group"] is True
    assert (
        reproduction["rescanned_first_hits_equal_facts"]
        == body["inputs"]["production_direct_hit_documents"]
    )
    assert reproduction["recomputed_synth_lineage_keys_equal_facts"] > 2000
    decisions = [
        json.loads(x)
        for x in next(run["root"].glob("scratch/*/decisions.jsonl")).read_bytes().splitlines()
    ]
    current = body["matrix"]["current|current_transitive"]["allocations"]
    for name, row in current.items():
        mine = [
            d
            for d in decisions
            if canonical.canonical_bytes(
                [d["component"], d["view"], d["upstream_component"]]
            ).decode()
            == name
        ]
        assert row["kept"] == sum(d["decision"] == "kept" for d in mine)
        assert row["excluded"] == sum(d["decision"] == "excluded" for d in mine)
        assert row["recovered_kept_documents"] == 0 and row["moved_into_train"] == 0


@pytest.mark.parametrize(
    "combination",
    [
        f"{c.name}|{lin}"
        for c in cand.CANDIDATES
        for lin in ("current_transitive", "query_seed_family", "query_seed_one_hop")
    ],
)
def test_every_cell_equals_the_independent_oracle(run: dict[str, Any], combination: str) -> None:
    got = run["body"]["matrix"][combination]["allocations"]
    expected = run["oracle"][combination]
    for name, row in expected.items():
        for field in ("kept", "duplicate", "excluded", "direct_hit_documents"):
            assert got[name][field] == row[field], (name, field)


def test_lineage_policies_bound_propagation_on_the_seed_chain(run: dict[str, Any]) -> None:
    matrix = run["body"]["matrix"]
    for candidate in ("current", "floor8"):
        a, b, c = (
            matrix[f"{candidate}|{lin}"]["allocations"][SYNTH]
            for lin in ("current_transitive", "query_seed_family", "query_seed_one_hop")
        )
        assert a["direct_hit_documents"] == b["direct_hit_documents"] == c["direct_hit_documents"]
        assert b["excluded"] < c["excluded"] < a["excluded"]
        assert a["excluded"] > 2000  # the transitive seed chain is one family
        assert b["excluded"] < 0.1 * a["excluded"]  # three ~50-row seed families
    lineage = run["body"]["lineage"]
    # The one transitive giant splits back into its 60 seed families.
    assert lineage["rebuilt_families_B"] - lineage["rebuilt_families_A"] == 59
    assert lineage["additional_seed_only_incidences"] > 0


def test_candidates_drop_common_short_phrases_but_keep_real_copies(run: dict[str, Any]) -> None:
    matrix = run["body"]["matrix"]
    synth = {
        c.name: matrix[f"{c.name}|query_seed_family"]["allocations"][SYNTH] for c in cand.CANDIDATES
    }
    # The long ARC item copy (seed 10) stays a hit under every candidate.
    assert all(row["direct_hit_documents"] >= 1 for row in synth.values())
    assert synth["floor8"]["direct_hit_documents"] < synth["current"]["direct_hit_documents"]
    gutenberg = {
        c.name: matrix[f"{c.name}|current_transitive"]["allocations"][GUTENBERG]
        for c in cand.CANDIDATES
    }
    assert (
        gutenberg["current"]["direct_hit_documents"] > gutenberg["floor8"]["direct_hit_documents"]
    )
    pressbooks = {
        c.name: matrix[f"{c.name}|current_transitive"]["allocations"][PRESSBOOKS]
        for c in cand.CANDIDATES
    }
    libretexts = {
        c.name: matrix[f"{c.name}|current_transitive"]["allocations"][LIBRETEXTS]
        for c in cand.CANDIDATES
    }
    # BLiMP pair copy: short sentences only; floor8 loses it, the pair rule keeps it.
    assert (
        pressbooks["floor8_pair"]["direct_hit_documents"]
        == pressbooks["floor8"]["direct_hit_documents"] + 1
    )
    # A single BLiMP sentence: a hit for current/prompt8 only.
    assert (
        libretexts["floor8_pair"]["direct_hit_documents"]
        == libretexts["floor8"]["direct_hit_documents"]
    )
    assert (
        libretexts["prompt8"]["direct_hit_documents"]
        == libretexts["floor8"]["direct_hit_documents"] + 1
    )
    totals = {name: matrix[f"{name}|query_seed_family"]["totals"] for name in synth}
    assert totals["floor8"]["kept"] > totals["prompt8"]["kept"] > totals["current"]["kept"]
    assert all(t["recovered_kept_documents"] >= 0 for t in totals.values())


def test_index_level_recall_by_task(run: dict[str, Any]) -> None:
    recall = run["body"]["benchmark_recall_index_level"]["items_by_candidate_and_task_split"]
    blimp = "blimp/authored-split"
    assert recall["current"][blimp] == {"items": 1, "standalone": 1, "pair_only": 0, "unsigned": 0}
    assert recall["floor8"][blimp]["unsigned"] == 1
    assert recall["floor8_pair"][blimp]["pair_only"] == 1
    for name in ("current", "floor8", "floor8_pair"):
        assert recall[name]["arc_easy/authored-split"]["standalone"] == 2
    # The 7-token sailor item (5-token context, yes/no endings) has no 8-token signature:
    # floor8 cannot detect even a full copy of it. The audit must show that loss.
    assert recall["current"]["hellaswag/authored-split"]["standalone"] == 2
    assert recall["floor8"]["hellaswag/authored-split"] == {
        "items": 2,
        "standalone": 1,
        "pair_only": 0,
        "unsigned": 1,
    }


def test_injected_copy_recall_and_render_slots(run: dict[str, Any]) -> None:
    injected = run["body"]["injected_copy_recall"]["by_task_split_and_form"]
    for group, forms in injected.items():
        composite = forms["item_composite"]
        assert composite["detected"]["current"] == composite["items"], group
    composites = {g: f["item_composite"]["detected"] for g, f in injected.items()}
    assert composites["hellaswag/authored-split"]["floor8_pair"] == 1  # the sailor item is lost
    assert composites["arc_easy/authored-split"]["floor8"] == 2
    assert composites["piqa/authored-split"]["floor8"] == 0  # 5-token goal, yes/no
    assert composites["blimp/authored-split"]["floor8_pair"] == 1
    blimp = injected["blimp/authored-split"]
    assert blimp["item_composite"]["detected"]["floor8"] == 0
    assert blimp["single_sentence"]["detected"]["floor8_pair"] == 0
    # The 7-token PROMPT question alone is no longer an exclusion trigger under floor8.
    arc_prompt = injected["arc_easy/authored-split"]["prompt_only"]
    assert arc_prompt["detected"]["current"] == 2 and arc_prompt["detected"]["floor8"] == 1
    top = run["body"]["top_patterns"][0]
    assert top["token_length"] == 4 and top["kinds"] == ["prompt"]
    assert top["triggers_alone_under"] == ["current"]
    assert top["render_slots"]["hellaswag.ctx_b"]["class"] == "published_subfield_fragment"
    assert top["allocations_hit"] >= 5 and top["first_hit_rank"] == 1


def test_short_pattern_census(run: dict[str, Any]) -> None:
    census = run["body"]["short_pattern_census"]
    assert census["short_3_to_5_token_patterns"] >= 3
    assert census["documents_whose_recorded_first_hit_is_3_to_5_tokens"][GUTENBERG] >= 1
    still = census["rescanned_documents_still_hit_by_candidate"]
    assert still["current"] == run["body"]["inputs"]["rescanned_documents"]
    assert still["floor8"] < still["floor8_pair"] <= still["prompt8"] < still["current"]


def test_report_and_progress_are_content_free(run: dict[str, Any]) -> None:
    out = (run["stdout"] + run["stderr"]).decode("utf-8")
    for secret in (COMMON, PROMPT, LONG_Q, GOOD, BAD, "wikipedia"):
        assert secret.casefold() not in out.casefold()
    assert "Seed_" not in out and "authored-document" not in out
    assert "://" not in out
    text = run["stderr"].decode("utf-8")
    seen = [s for s in STAGES if f"[POLICY AUDIT] {s}" in text]
    assert seen == STAGES
    assert "ETA" in text


def test_worker_count_never_changes_the_report(run: dict[str, Any]) -> None:
    done = invoke(
        *audit_args(
            run,
            "--workers",
            "1",
            "--no-progress",
            "--benchmark-material",
            str(run["root"] / "material"),
        )
    )
    assert done.returncode == 0 and done.stderr == b""
    one = {k: v for k, v in json.loads(done.stdout).items() if k != "resources"}
    four = {k: v for k, v in run["body"].items() if k != "resources"}
    assert one == four


def test_refusals_are_content_free(run: dict[str, Any], tmp_path: Path) -> None:
    ledger = next(run["root"].glob("scratch/*/decisions.jsonl"))
    short = tmp_path / "short.jsonl"
    short.write_bytes(b"".join(ledger.read_bytes().splitlines(keepends=True)[:-1]))
    done = invoke(*audit_args(run, "--decisions", str(short), "--no-progress"))
    assert done.returncode == 1 and done.stdout == b""
    record = json.loads(done.stderr)
    assert record["stage"] == "DECISION LEDGER" and "row count" in record["reason"]
    index = tmp_path / "index.jsonl"
    shutil.copyfile(run["root"] / "prepared/index.jsonl", index)
    with index.open("ab") as stream:
        stream.write(b"\n")
    arguments = audit_args(run, "--no-progress")
    arguments[arguments.index("--benchmark-index") + 1] = str(index)
    done = invoke(*arguments)
    assert done.returncode == 1 and done.stdout == b""
    assert "benchmark receipt" in json.loads(done.stderr)["reason"]
    existing = invoke(*audit_args(run, "--no-progress", "--state-out", str(run["state"])))
    assert existing.returncode == 1 and "state output" in json.loads(existing.stderr)["reason"]


def test_rescan_scope_is_conservative(run: dict[str, Any]) -> None:
    done = invoke(
        *audit_args(run, "--no-progress", "--rescan-allocation", "synth_en_explanations/default/-")
    )
    assert done.returncode == 0
    body = json.loads(done.stdout)
    narrow = body["matrix"]["floor8|query_seed_family"]
    full = run["body"]["matrix"]["floor8|query_seed_family"]
    # Split reallocation is global, so only the exclusion columns are compared.
    for field in ("kept", "excluded", "duplicate", "direct_hit_documents"):
        assert narrow["allocations"][SYNTH][field] == full["allocations"][SYNTH][field]
    gut = narrow["allocations"][GUTENBERG]
    assert gut["conservative_unverified_hits"] > 0
    assert gut["kept"] <= full["allocations"][GUTENBERG]["kept"]


# -- stage 2 -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def projection(run: dict[str, Any]) -> dict[str, Any]:
    root = run["root"]
    done = invoke(
        "project-tokens",
        "--state",
        str(run["state"]),
        "--plan",
        str(run["plan"]),
        "--counts",
        str(root / "counts/counts.json"),
        "--deficit-report",
        str(root / "deficit.json"),
        "--tokenizer",
        str(run["tokenizer"]),
        "--workers",
        "2",
        "--progress-interval",
        "0.01",
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-2000:]
    return {"body": json.loads(done.stdout), "stderr": done.stderr}


def test_projection_is_exact_under_the_current_tokenizer(
    run: dict[str, Any], projection: dict[str, Any]
) -> None:
    body = projection["body"]
    assert body["label"] == "counterfactual supply under current tokenizer"
    assert body["token_allocations"] == [SYNTH]
    current = body["combinations"]["current|current_transitive"]
    counts = canonical.loads_bytes_strict((run["root"] / "counts/counts.json").read_bytes())[
        "payload"
    ]
    for name, row in current["allocations"].items():
        assert row["projected_valid_targets"] == counts["allocations"][name]["valid_targets"]
    assert current["all_frozen_quotas_met"] is False and SYNTH in current["remaining_deficits"]
    # Re-tokenize every SYNTH document entering train under floor8 x query_seed_family.
    header, state = supply.load_state(run["state"])
    combo = header["combinations"].index("floor8|query_seed_family")
    plan = canonical.loads_bytes_strict(run["plan"].read_bytes())
    plan = plan.get("payload", plan)
    tokenizer = load_tokenizer(run["tokenizer"])
    synth = header["allocations"].index(SYNTH)
    enter = np.flatnonzero(
        (state["allocation"] == synth)
        & ~state["production_train"]
        & ((state["train_bits"] >> combo) & 1).astype(bool)
    )
    texts = []
    for k in enter.tolist():
        item = plan["files"][int(state["plan_file"][k])]
        line = (
            (Path(plan["data_root"]) / item["path"]).read_bytes().splitlines()[int(state["row"][k])]
        )
        texts.append(json.loads(line)["text"])
    row = body["combinations"]["floor8|query_seed_family"]["allocations"][SYNTH]
    assert row["moved_into_train_documents"] == len(texts) > 2000
    assert row["moved_into_train_valid_targets"] == sum(tokenizer.count_valid_targets(texts))
    assert row["projection"] == "exact" and row["status"] == "SUFFICIENT"
    assert "[TOKEN SUPPLY] TOKENIZE" in projection["stderr"].decode()


def test_projection_refuses_a_different_tokenizer(run: dict[str, Any], tmp_path: Path) -> None:
    root = run["root"]
    done = invoke(
        "project-tokens",
        "--state",
        str(run["state"]),
        "--plan",
        str(run["plan"]),
        "--counts",
        str(root / "counts/counts.json"),
        "--deficit-report",
        str(root / "deficit.json"),
        "--tokenizer",
        str(run["tokenizer"]),
        "--expect-fingerprint",
        "0" * 64,
        "--no-progress",
    )
    assert done.returncode == 1 and done.stdout == b""
    assert "fingerprint" in json.loads(done.stderr)["reason"]
