"""Authored C05 run and an independent oracle for the contamination-policy audit tests.

The fixture is the actual authored C05 flow (``scripts.c05_synthetic_flow``) with:

* a benchmark whose HellaSwag item has a 4-token ``ctx_b`` fragment (``COMMON``), a BLiMP
  pair of 4-token sentences, a 13-token ARC question with options, and the flow's own
  7-token ARC question (``PROMPT``) with yes/no options;
* SYNTH rows on 60 seed articles chained by ``additional_seed_url`` (one transitive
  family), one SYNTH row carrying ``COMMON`` (seed 5) and one carrying a full copy of the
  long ARC item (seed 10, a true contamination);
* ``COMMON`` planted as ordinary prose in every ninth clean row of the other allocations,
  a BLiMP pair copy in PressBooks and a single BLiMP sentence in LibreTexts;
* the flow's own planted ``PROMPT`` wrapper row per allocation and cross-allocation copies.

``oracle`` recomputes every matcher x lineage decision from the corpus, the protected
index and the ledger's duplicate groups with naive, independent code.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from scripts import c05_synthetic_flow as flow_module
from scripts.c05_authored_pilot import KEY, PROMPT
from scripts.c05_synthetic_flow import (
    ISSUER,
    KEY_ENV,
    decide_and_plan,
    fit_tokenizer,
    prepare,
    resources,
    run_c05,
)

from test_c05_component_forensics import seed_metadata
from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.lineage import lineage_keys_v3
from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import candidates as cand
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import MatcherPolicy
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.protected import MaterialSpec, build
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.streaming import informative

COMMON = "then the man begins"
LONG_Q = "Which property of a material describes how easily heat flows through it?"
LONG_OPTIONS = ["thermal conductivity", "density", "mass", "color"]
GOOD, BAD = "Those clever owls sing.", "Those clever owls sings."
SYNTH = '["synth_en_explanations","default",null]'
# Frozen SYNTH valid-target quota: unmet by the current C05, met once SYNTH is recovered.
SYNTH_QUOTA = 200_000
LINEAGES = ("current_transitive", "query_seed_family", "query_seed_one_hop")
MATERIAL: dict[str, list[dict[str, Any]]] = {
    "arc_easy": [
        {"question": PROMPT, "choices": {"text": ["yes", "no"]}},
        {"question": LONG_Q, "choices": {"text": LONG_OPTIONS}},
    ],
    "piqa": [{"goal": "Fasten a loose wooden shelf", "sol1": "yes", "sol2": "no"}],
    "blimp": [{"sentence_good": GOOD, "sentence_bad": BAD}],
    "hellaswag": [
        {"ctx": "A sailor repairs the sail.", "endings": ["yes", "no"]},
        {
            "ctx_a": "A man is standing on a ladder next to a house.",
            "ctx_b": COMMON,
            "ctx": f"A man is standing on a ladder next to a house. {COMMON}",
            "activity_label": "Painting",
            "endings": [
                "to paint the wooden shutters with a wide brush.",
                "to sing loudly while the dog runs around the yard.",
            ],
        },
    ],
}


def prepare_benchmark(root: Path) -> None:
    material = root / "material"
    material.mkdir()
    pins = benchmark_requirements(Path("manifests/eval_dataset_pins.yaml"))["tasks"]
    entries = []
    for task, rows in MATERIAL.items():
        path = material / (task + ".jsonl")
        path.write_bytes(b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows))
        entries.append(
            {
                "task": task,
                "repository": pins[task]["repository"],
                "revision": pins[task]["revision"],
                "config": "authored-config",
                "split": "authored-split",
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": file_sha(path),
                "items": len(rows),
            }
        )
    spec = MaterialSpec.model_validate(
        {
            "files": entries,
            "publisher_inventory_sha256": canonical.digest(entries),
            "all_published_configs_splits_reviewed": True,
            "isolation": {
                "mode": "authored",
                "operator_principal": "fixture-operator",
                "denied_agent_principal": "fixture-agent",
                "attestation_sha256": canonical.digest("authored"),
                "access_controls_verified": True,
            },
        }
    )
    identity = implementation_identity()
    build(
        spec,
        material,
        root / "prepared",
        policy=MatcherPolicy(),
        resources=resources(),
        issuer=ISSUER,
        key=KEY.encode(),
        code_commit=identity["code_commit"],
        code_identity=identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )


def allocation_of_numbers(allocations: list[tuple[str, str, str, str | None, int]]) -> list[str]:
    """The flow numbers rows sequentially: quota // 40 + 6 clean rows, then 2 planted."""
    out: list[str] = []
    for component, _source, view, upstream, quota in allocations:
        key = canonical.canonical_bytes([component, view, upstream]).decode()
        out.extend([key] * (quota // flow_module.TARGETS_PER_RECORD + 8))
    return out


def build_run(root: Path) -> dict[str, Any]:
    monkey = pytest.MonkeyPatch()
    monkey.setenv(KEY_ENV, KEY)
    original = flow_module.doc
    allocations = [
        (c, s, v, u, 120_000 if c == "synth_en_explanations" else q)
        for c, s, v, u, q in flow_module.ALLOCATIONS
    ]
    owner = allocation_of_numbers(allocations)
    planted: dict[str, list[int]] = defaultdict(list)
    first_clean: dict[str, int] = {}
    for number, key in enumerate(owner):
        first_clean.setdefault(key, number)

    def doc(number: int, source: str, text: str, metadata: dict[str, Any] | None = None) -> Any:
        key = owner[number]
        clean = PROMPT not in text and not text.startswith("Generated")
        if source == "synth":
            metadata = seed_metadata("chain", number, text)
            plain = clean and "query_seed_url" in metadata and "additional_seed_url" not in metadata
            if plain and number % 60 == 5 and not planted["synth_common"]:
                text = f"{text} and {COMMON} again"
                planted["synth_common"].append(number)
            elif plain and number % 60 == 10 and not planted["synth_long"]:
                text = f"{text} {LONG_Q} {' '.join(LONG_OPTIONS)} end"
                planted["synth_long"].append(number)
        elif clean and number == first_clean[key] and "pressbooks" in key:
            text = f"{text} {GOOD} {BAD} end"
            planted["pair"].append(number)
        elif clean and number == first_clean[key] and "libretexts" in key:
            text = f"{text} {GOOD} end"
            planted["single"].append(number)
        elif clean and number % 9 == 4:
            text = f"{text} and {COMMON} again"
            planted["common"].append(number)
        return original(number, source, text, metadata)

    finals = {**flow_module.FINALS, "synth_en_explanations": SYNTH_QUOTA}
    monkey.setattr(flow_module, "FINALS", finals)
    monkey.setattr(flow_module, "TOTAL", sum(finals.values()))
    monkey.setattr(flow_module, "doc", doc)
    monkey.setattr(flow_module, "ALLOCATIONS", allocations)
    monkey.setattr(flow_module, "prepare_benchmark", prepare_benchmark)
    try:
        paths = prepare(root)
        plan_path = decide_and_plan(paths)
        c05 = run_c05(paths, plan_path)
        tokenizer = root / "tokenizer"
        fit_tokenizer(c05["proof"], tokenizer)
        common = ["--c05-proof", str(c05["proof"]), "--tokenizer", str(tokenizer)]
        signing = ["--issuer", ISSUER, "--key-env", KEY_ENV]
        scratch = ["--scratch", str(root / "allocation-scratch")]
        if operator(
            ["count-tokens", *common, *scratch, "--output", str(root / "counts"), *signing]
        ):
            raise RuntimeError("authored count-tokens refused")
        operator(
            [
                "select",
                *common,
                *scratch,
                "--counts",
                str(root / "counts"),
                "--quotas",
                str(paths["quotas"]),
                "--ifm-split",
                str(root / "ifm-split.json"),
                "--deficit-report",
                str(root / "deficit.json"),
                "--output",
                str(root / "selection"),
                *signing,
            ]
        )
        if not (root / "deficit.json").is_file():
            raise RuntimeError("authored selection was expected to report a SYNTH deficit")
    finally:
        monkey.undo()
        os.environ.pop(KEY_ENV, None)
    return {"root": root, "plan": plan_path, "planted": dict(planted), "tokenizer": tokenizer}


# -- independent oracle ------------------------------------------------------------------------


class Union:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def oracle(root: Path, plan_path: Path) -> dict[str, dict[str, dict[str, int]]]:
    """Per combination and allocation: kept / duplicate / excluded / direct hits."""
    plan = canonical.loads_bytes_strict(plan_path.read_bytes())
    plan = plan.get("payload", plan)
    data = Path(plan["data_root"])
    docs: list[CanonicalDocument] = []
    allocs: list[str] = []
    for f in plan["files"]:
        key = canonical.canonical_bytes(
            [f["component"], f["view"], f["upstream_component"]]
        ).decode()
        for line in (data / f["path"]).read_bytes().splitlines():
            docs.append(CanonicalDocument(**json.loads(line)))
            allocs.append(key)
    ledger = {
        row["doc_id"]: row
        for row in map(
            json.loads, next((root / "scratch").rglob("decisions.jsonl")).read_bytes().splitlines()
        )
    }
    receipt = canonical.loads_bytes_strict(
        (root / "prepared/benchmark-preparation.receipt.json").read_bytes()
    )
    receipt = receipt.get("payload", receipt)
    refs: dict[str, int] = {}
    for entry in receipt["files"]:
        for n in range(1, entry["items"] + 1):
            refs[canonical.digest([entry, n])] = len(refs)
    patterns: dict[tuple[str, ...], dict[str, Any]] = {}
    for line in (root / "prepared/index.jsonl").read_bytes().splitlines():
        record = json.loads(line)
        info = patterns.setdefault(tuple(record["tokens"]), {"kinds": set(), "items": set()})
        for p in record["provenance"]:
            ref, _, kind = p.rpartition(":")
            info["kinds"].add(kind)
            info["items"].add(refs[ref])
    plist = list(patterns)

    def standalone(p: tuple[str, ...], candidate: cand.Candidate) -> bool:
        if candidate.floors is None:
            return True
        return any(informative(p, candidate.floors[k]) for k in patterns[p]["kinds"])

    hits: dict[str, list[bool]] = {}
    for candidate in cand.CANDIDATES:
        out = []
        for doc in docs:
            tokens = match_tokens(doc.text)
            found = [
                (p, s, s + len(p))
                for p in plist
                for s in range(len(tokens) - len(p) + 1)
                if tuple(tokens[s : s + len(p)]) == p
            ]
            hit = any(standalone(p, candidate) for p, _, _ in found)
            if not hit and candidate.pair is not None:
                short = [o for o in found if not standalone(o[0], candidate)]
                rule = candidate.pair
                hit = any(
                    a[0] != b[0]
                    and patterns[a[0]]["items"] & patterns[b[0]]["items"]
                    and (a[2] <= b[1] or b[2] <= a[1])
                    and max(a[2], b[2]) - min(a[1], b[1]) <= rule.window_tokens
                    and len(a[0]) + len(b[0]) >= rule.min_covered_tokens
                    for a in short
                    for b in short
                )
            out.append(hit)
        hits[candidate.name] = out

    n = len(docs)
    index_of = {d.doc_id: k for k, d in enumerate(docs)}
    groups: dict[str, list[int]] = defaultdict(list)
    for k, d in enumerate(docs):
        groups[ledger[d.doc_id]["duplicate_group"]].append(k)
    survivor = [False] * n
    for members in groups.values():
        best = min(
            members,
            key=lambda k: (
                -docs[k].utf8_byte_count,
                docs[k].source_id.encode(),
                docs[k].doc_id.encode(),
            ),
        )
        survivor[best] = True

    def without_additional(d: CanonicalDocument) -> tuple[str, ...]:
        from dataclasses import replace

        metadata = {k: v for k, v in d.source_metadata.items() if k != "additional_seed_url"}
        return lineage_keys_v3(replace(d, source_metadata=metadata))

    full_keys = [lineage_keys_v3(d) for d in docs]
    seed_keys = [without_additional(d) for d in docs]

    def families(keys: list[tuple[str, ...]]) -> Union:
        u = Union(n)
        for members in groups.values():
            for k in members[1:]:
                u.union(members[0], k)
        first: dict[str, int] = {}
        for k, ks in enumerate(keys):
            for key in ks:
                u.union(first.setdefault(key, k), k)
        for k, d in enumerate(docs):
            for p in d.parent_ids:
                if p in index_of:
                    u.union(k, index_of[p])
        return u

    fam_a, fam_b = families(full_keys), families(seed_keys)
    dropped = defaultdict(set)
    for k in range(n):
        for key in set(full_keys[k]) - set(seed_keys[k]):
            dropped[key].add(k)
    touching: dict[str, set[int]] = defaultdict(set)
    for k in range(n):
        for key in full_keys[k]:
            if key in dropped:
                touching[key].add(k)

    result: dict[str, dict[str, dict[str, int]]] = {}
    for candidate in cand.CANDIDATES:
        for lineage in LINEAGES:
            u = fam_a if lineage == LINEAGES[0] else fam_b
            hit_roots = {u.find(k) for k in range(n) if hits[candidate.name][k]}
            if lineage == LINEAGES[2]:
                extra = set()
                for members in touching.values():
                    roots = {u.find(k) for k in members}
                    if roots & hit_roots:
                        extra |= roots
                hit_roots |= extra
            table: dict[str, dict[str, int]] = defaultdict(
                lambda: {"kept": 0, "duplicate": 0, "excluded": 0, "direct_hit_documents": 0}
            )
            for k in range(n):
                row = table[allocs[k]]
                if u.find(k) in hit_roots:
                    row["excluded"] += 1
                elif survivor[k]:
                    row["kept"] += 1
                else:
                    row["duplicate"] += 1
                row["direct_hit_documents"] += hits[candidate.name][k]
            result[f"{candidate.name}|{lineage}"] = dict(table)
    return result
