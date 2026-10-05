"""Read-only, content-free forensics of one C05 exclusion family (connected component).

C05 (``exclusion.grouping``) builds two partitions over every input document:

* ``dup`` (ledger ``duplicate_group``): connected components of exact-hash and
  near-duplicate (MinHash, estimated Jaccard >= threshold) edges;
* ``fam`` (ledger ``lineage_group``): connected components of ``dup`` plus
  lineage-key edges (two documents sharing an identical ``lineage_keys_v3`` string)
  plus parent edges (a ``parent_ids`` entry equal to another document's id).

``publish`` marks a document ``excluded`` when ANY document of its ``fam`` component has
a direct benchmark hit; otherwise the ``dup`` survivor is ``kept`` and the rest are
``duplicate``. This tool rebuilds one excluded ``fam`` component from the ledger and the
C05 fact units (the exact keys, parent references and direct hits C05 used), classifies
every edge, and replays the union in stages to show which edge class creates the
component and how much exclusion each class propagates.

Reads: the C05 plan, ``decisions.jsonl``, ``facts/*.unit`` (C05 scratch; X: attached),
optionally the cleaned corpus (``--read-corpus``: SYNTH seed-URL field attribution and
categorical metadata) and the protected index (``--benchmark-index``: hit pattern kind
and token length). Prints ONE JSON object to stdout and writes nothing.

Never printed: document text, benchmark text, document/group ids, raw URLs, host
names other than a fixed public class, per-record hashes. Identifiers appear only as
salted HMAC-SHA256 fingerprints (fresh random salt per run unless ``--salt-env``), so a
dominant identifier is visible as a count without being recoverable.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from xlm.data.dedup.lineage import canonical_url
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.factstore import FACTS_DIR, open_unit
from xlm.data.exclusion.inputs import read_metadata

LABEL = re.compile(r"^[a-z0-9_\-]{1,32}$")
URL_FIELDS = ("query_seed_url", "additional_seed_url")
METADATA_URL_FIELDS = ("url", "source_url", "canonical_url")


def key_of(component: str, view: str, upstream: str | None) -> str:
    return str(canonical.canonical_bytes([component, view, upstream]).decode())


class Fingerprint:
    def __init__(self, salt: bytes) -> None:
        self.salt = salt

    def __call__(self, value: bytes) -> str:
        return hmac.new(self.salt, value, hashlib.sha256).hexdigest()[:16]


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n
        self.components = n

    def find(self, x: int) -> int:
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        self.components -= 1
        return True

    def largest(self) -> int:
        return max(
            (self.size[i] for i in range(len(self.parent)) if self.parent[i] == i), default=0
        )


def ledger(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as stream:
        for raw in stream:
            yield json.loads(raw)


def classify_key(key: bytes, own: bytes) -> str:
    if key.startswith(b"url:"):
        return "url"
    if key.startswith(b"parent:"):
        return "parent_self" if key.endswith(b":" + own) else "parent_ref"
    if key.startswith(b"metadata:"):
        parts = key[len(b"metadata:") :].split(b":", 3)
        rule = parts[2].decode("ascii", "replace") if len(parts) > 2 else "unknown"
        return "metadata:" + (rule if LABEL.match(rule) else "unknown")
    return "other"


def url_shape(key: bytes) -> dict[str, Any]:
    """Content-free structure of a canonical URL key ``url:host/path?query``."""
    text = key[len(b"url:") :].decode("utf-8", "replace").removeprefix("//")
    host, _, rest = text.partition("/")
    path, _, query = rest.partition("?")
    segments = [s for s in path.split("/") if s]
    if host.endswith("wikipedia.org"):
        host_class = "wikipedia"
    elif host.endswith(("wikimedia.org", "wikidata.org", "wikibooks.org", "wiktionary.org")):
        host_class = "other_wikimedia"
    elif host in ("huggingface.co", "github.com", "arxiv.org"):
        host_class = host.split(".")[0]
    elif not host:
        host_class = "empty"
    else:
        host_class = "other"
    return {
        "host_class": host_class,
        "path_segments": len(segments),
        "has_query": bool(query),
        "domain_level": not segments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="the C05 plan (pNNNN.json)")
    parser.add_argument("--decisions", type=Path, help="default: <scratch>/<plan>/decisions.jsonl")
    parser.add_argument("--facts", type=Path, help="default: <scratch>/<plan>/facts")
    parser.add_argument("--component-rank", type=int, default=1, help="1 = largest excluded")
    parser.add_argument(
        "--focus-allocation",
        action="append",
        default=[],
        metavar="COMPONENT/VIEW/UPSTREAM",
        help="per-allocation exclusion analysis (use '-' for a null upstream)",
    )
    parser.add_argument("--read-corpus", action="store_true", help="attribute seed-URL fields")
    parser.add_argument("--benchmark-index", type=Path, help="protected index.jsonl (optional)")
    parser.add_argument("--salt-env", help="env var holding a fingerprint salt (default random)")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--removal-probes", type=int, default=5)
    args = parser.parse_args()

    plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
    work = Path(plan.scratch_root) / plan.identity()
    decisions_path = args.decisions or work / "decisions.jsonl"
    facts = args.facts or work / FACTS_DIR
    salt = os.environ[args.salt_env].encode() if args.salt_env else os.urandom(32)
    fp = Fingerprint(salt)
    ordinal = {f.path: n for n, f in enumerate(plan.files)}
    file_alloc = {f.path: key_of(f.component, f.view, f.upstream_component) for f in plan.files}
    focus = set()
    for item in args.focus_allocation:
        component, view, upstream = item.split("/")
        focus.add(key_of(component, view, None if upstream == "-" else upstream))

    # -- pass 1: decisions per allocation, excluded family sizes ---------------------------
    by_allocation: dict[str, Counter[str]] = defaultdict(Counter)
    excluded_families: Counter[str] = Counter()
    excluded_family_bytes: Counter[str] = Counter()
    for row in ledger(decisions_path):
        allocation = key_of(row["component"], row["view"], row["upstream_component"])
        by_allocation[allocation][row["decision"]] += 1
        if row["decision"] == "excluded":
            excluded_families[row["lineage_group"]] += 1
            excluded_family_bytes[row["lineage_group"]] += int(row["bytes"])
    ranked = excluded_families.most_common(args.component_rank)
    if len(ranked) < args.component_rank:
        raise SystemExit("fewer excluded families than the requested rank")
    target = ranked[-1][0]

    # -- pass 2: members of the target component and every row of focus allocations ---------
    member_index: dict[tuple[str, int], int] = {}
    member_alloc: list[str] = []
    member_bytes: list[int] = []
    member_dup: list[str] = []
    focus_rows: dict[tuple[str, int], dict[str, Any]] = {}
    component_rows: dict[tuple[str, int], tuple[str, bool]] = {}  # allocations in the target
    target_allocations: set[str] = set()
    for row in ledger(decisions_path):
        allocation = key_of(row["component"], row["view"], row["upstream_component"])
        where = (row["file"], int(row["row"]) - 1)
        if row["lineage_group"] == target:
            member_index[where] = len(member_alloc)
            member_alloc.append(allocation)
            member_bytes.append(int(row["bytes"]))
            member_dup.append(row["duplicate_group"])
            target_allocations.add(allocation)
        if allocation in focus:
            focus_rows[where] = {
                "decision": row["decision"],
                "bytes": int(row["bytes"]),
                "family_size": excluded_families.get(row["lineage_group"], 0),
                "family": row["lineage_group"],
            }
    if args.read_corpus:
        for row in ledger(decisions_path):
            allocation = key_of(row["component"], row["view"], row["upstream_component"])
            if allocation in target_allocations:
                component_rows[(row["file"], int(row["row"]) - 1)] = (
                    row["decision"],
                    row["lineage_group"] == target,
                )
    n = len(member_alloc)

    # -- fact units: exact lineage keys, parent references, direct hits ---------------------
    key_ids: dict[bytes, int] = {}
    key_class: list[str] = []
    postings: list[list[int]] = []  # key -> members (only target members)
    member_ids: list[bytes] = [b""] * n
    member_parents: list[tuple[bytes, ...]] = [()] * n
    direct: dict[int, bytes] = {}
    focus_hits: dict[tuple[str, int], bytes] = {}
    all_hits_by_allocation: Counter[str] = Counter()
    files = sorted({f for f, _ in member_index} | {f for f, _ in focus_rows})
    members_by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for (path, r), m in member_index.items():
        members_by_file[path].append((r, m))
    for item in plan.files:
        unit_path = facts / f"{ordinal[item.path]:05d}.unit"
        if not unit_path.is_file():
            continue
        unit = open_unit(unit_path)
        hits = unit.hits()
        all_hits_by_allocation[file_alloc[item.path]] += int(hits.shape[0])
        if item.path not in files:
            unit.close()
            continue
        hit_rows = {int(h["row"]): bytes(h["pattern"]) for h in hits}
        ids_blob, ids_off = unit.ids()
        blob, offsets, counts, _ = unit.strings("lineage")
        pblob, poffsets, pcounts, _ = unit.strings("parents")
        starts = [0]
        for c in counts.tolist():
            starts.append(starts[-1] + int(c))
        pstarts = [0]
        for c in pcounts.tolist():
            pstarts.append(pstarts[-1] + int(c))
        for r, m in members_by_file.get(item.path, []):
            own = bytes(ids_blob[int(ids_off[r]) : int(ids_off[r + 1])])
            member_ids[m] = own
            for k in range(starts[r], starts[r + 1]):
                text = bytes(blob[int(offsets[k]) : int(offsets[k + 1])])
                index = key_ids.get(text)
                if index is None:
                    index = key_ids[text] = len(postings)
                    postings.append([])
                    key_class.append(classify_key(text, own))
                postings[index].append(m)
            member_parents[m] = tuple(
                bytes(pblob[int(poffsets[k]) : int(poffsets[k + 1])])
                for k in range(pstarts[r], pstarts[r + 1])
            )
            if r in hit_rows:
                direct[m] = hit_rows[r]
        for where in focus_rows:
            if where[0] == item.path and where[1] in hit_rows:
                focus_hits[where] = hit_rows[where[1]]
        unit.close()
    key_bytes = list(key_ids)

    # -- optional: which metadata field produced each URL key (SYNTH seed URLs) -------------
    field_of: dict[tuple[int, int], set[str]] = defaultdict(set)  # (member, key) -> fields
    corpus: dict[str, Any] = {}
    if args.read_corpus:
        root = Path(plan.data_root)
        presence: dict[str, Counter[str]] = defaultdict(Counter)
        exercise: dict[str, Counter[str]] = defaultdict(Counter)
        recomputed_ok = recomputed_bad = 0
        by_file: dict[str, list[tuple[int, tuple[str, bool]]]] = defaultdict(list)
        for (path, r), state_of in component_rows.items():
            by_file[path].append((r, state_of))
        for path, file_rows in by_file.items():
            wanted = dict(file_rows)
            with (root / path).open("rb") as stream:
                for r, line in enumerate(stream):
                    if r not in wanted:
                        continue
                    metadata = json.loads(line).get("source_metadata") or {}
                    decision, inside = wanted[r]
                    state = "target_component" if inside else decision
                    flags: list[str] = []
                    keys_here: dict[bytes, set[str]] = defaultdict(set)
                    for name in URL_FIELDS + METADATA_URL_FIELDS:
                        raw_url = metadata.get(name)
                        if not isinstance(raw_url, str) or not raw_url.strip():
                            continue
                        parsed = urlsplit(raw_url) if name in URL_FIELDS else None
                        if parsed is not None and not (
                            parsed.scheme.lower() in ("http", "https") and parsed.netloc
                        ):
                            flags.append(name + ":non_http")
                            continue
                        flags.append(name)
                        keys_here[b"url:" + canonical_url(raw_url).encode("utf-8")].add(name)
                    presence[state]["+".join(sorted(flags)) or "no_url_metadata"] += 1
                    label = metadata.get("exercise")
                    exercise[state][
                        label if isinstance(label, str) and LABEL.match(label) else "other/absent"
                    ] += 1
                    member = member_index.get((path, r))
                    if member is None:
                        continue
                    for text, names in keys_here.items():
                        found = key_ids.get(text)
                        if found is None:
                            recomputed_bad += 1
                            continue
                        recomputed_ok += 1
                        field_of[(member, found)] |= names
        corpus = {
            "url_metadata_presence_by_state": {k: dict(v) for k, v in sorted(presence.items())},
            "exercise_label_by_state": {
                k: dict(v.most_common(args.top)) for k, v in sorted(exercise.items())
            },
            "recomputed_url_keys_found_in_units": recomputed_ok,
            "recomputed_url_keys_missing_from_units": recomputed_bad,
        }

    # -- edge classes ----------------------------------------------------------------------
    def incidence_class(k: int, m: int) -> str:
        base = key_class[k]
        if base != "url" or not field_of:
            return base
        names = field_of.get((m, k), set())
        if names == {"query_seed_url"}:
            return "url:query_seed_url"
        if names == {"additional_seed_url"}:
            return "url:additional_seed_url"
        if "query_seed_url" in names:
            return "url:query_seed_url"  # same row names the seed in both fields
        return "url:" + ("+".join(sorted(names)) or "unattributed")

    dup_members: dict[str, list[int]] = defaultdict(list)
    for m, group in enumerate(member_dup):
        dup_members[group].append(m)
    id_to_member = {member_ids[m]: m for m in range(n)}
    parent_edges = [
        (m, id_to_member[p]) for m in range(n) for p in member_parents[m] if p in id_to_member
    ]

    # Star edges per class: every incidence links its member to the key's first member.
    edges: dict[str, list[tuple[int, int, int]]] = defaultdict(list)  # class -> (a, b, key)
    for group_members in dup_members.values():
        for m in group_members[1:]:
            edges["duplicate"].append((group_members[0], m, -1))
    for a, b in parent_edges:
        edges["parent_edge"].append((a, b, -1))
    for k, members in enumerate(postings):
        if len(members) < 2:
            continue
        # Rows naming a URL as their own seed form the seed family first; every other
        # incidence (an additional seed, other URL metadata) is an edge to that anchor,
        # so the class that bridges families owns the merges it causes.
        seeds = [m for m in members if incidence_class(k, m) == "url:query_seed_url"]
        anchor = seeds[0] if seeds else members[0]
        for m in members:
            if m != anchor:
                edges[incidence_class(k, m)].append((anchor, m, k))

    def stage_rank(cls: str) -> int:
        fixed = {"duplicate": 0, "parent_edge": 1, "url:query_seed_url": 3}
        return fixed.get(cls, 4 if cls.startswith("url") else 2)

    order = sorted(edges, key=lambda c: (stage_rank(c), c))
    hit_members = set(direct)

    def excluded_by(uf: UnionFind) -> int:
        roots = {uf.find(m) for m in hit_members}
        return sum(1 for m in range(n) if uf.find(m) in roots)

    stages = []
    uf = UnionFind(n)
    for cls in order:
        merges = sum(uf.union(a, b) for a, b, _ in edges[cls])
        stages.append(
            {
                "after_class": cls,
                "candidate_edges": len(edges[cls]),
                "merges": merges,
                "components": uf.components,
                "largest_component": uf.largest(),
                "would_be_excluded": excluded_by(uf),
            }
        )
    reconstruction_complete = uf.components == 1
    alone = {}
    for cls in order:
        single = UnionFind(n)
        for a, b, _ in edges[cls]:
            single.union(a, b)
        alone[cls] = {"components": single.components, "largest_component": single.largest()}

    # Hit roots: components at the stage before URL/lineage bridging (dup + parent + same seed).
    seed_stage = UnionFind(n)
    for cls in order:
        if cls in ("duplicate", "parent_edge", "url:query_seed_url"):
            for a, b, _ in edges[cls]:
                seed_stage.union(a, b)
    hit_roots = {seed_stage.find(m) for m in hit_members}
    # Derivation counterfactuals (non-transitive): rows in a seed family (dup + parent +
    # same seed) that holds a direct hit, plus rows that name such a family's seed as an
    # ADDITIONAL seed (one hop). Never chained further.
    same_seed = {m for m in range(n) if seed_stage.find(m) in hit_roots}
    one_hop = set(same_seed)
    for cls, items in edges.items():
        if cls.startswith("url:") and cls != "url:query_seed_url":
            for anchor, m, _ in items:
                if seed_stage.find(anchor) in hit_roots:
                    one_hop.add(m)
    dup_only = UnionFind(n)
    for cls in ("duplicate", "parent_edge"):
        for a, b, _ in edges.get(cls, []):
            dup_only.union(a, b)
    counterfactuals = {
        "duplicate_and_parent_only": excluded_by(dup_only),
        "same_seed_family": len(same_seed),
        "same_seed_family_plus_one_hop_additional_seed": len(one_hop),
        "current_transitive_family": n,
    }

    # Dominant keys and single-key removal probes.
    key_sizes = sorted(
        ((len(set(p)), k) for k, p in enumerate(postings) if len(set(p)) > 1), reverse=True
    )
    dominant = []
    for size, k in key_sizes[: args.top]:
        entry: dict[str, Any] = {
            "fingerprint": fp(key_bytes[k]),
            "class": key_class[k],
            "members": size,
            "allocations": dict(Counter(member_alloc[m] for m in set(postings[k]))),
        }
        if key_class[k] == "url":
            entry["shape"] = url_shape(key_bytes[k])
            if field_of:
                entry["fields"] = dict(
                    Counter(
                        "+".join(sorted(field_of.get((m, k), set()))) or "unattributed"
                        for m in set(postings[k])
                    )
                )
        dominant.append(entry)
    probes = []
    for _, k in key_sizes[: args.removal_probes]:
        probe = UnionFind(n)
        for cls in order:
            for a, b, key in edges[cls]:
                if key != k:
                    probe.union(a, b)
        probes.append(
            {
                "removed_fingerprint": fp(key_bytes[k]),
                "components": probe.components,
                "largest_component": probe.largest(),
            }
        )

    # Posting-size histogram per class (how many keys join how many members).
    histogram: dict[str, Counter[str]] = defaultdict(Counter)
    for k, p in enumerate(postings):
        size = len(set(p))
        bucket = (
            "1"
            if size == 1
            else "2"
            if size == 2
            else "3-10"
            if size <= 10
            else ("11-100" if size <= 100 else "101-1000" if size <= 1000 else ">1000")
        )
        histogram[key_class[k]][bucket] += 1
    pairs: dict[str, Counter[str]] = defaultdict(Counter)
    for cls, items in edges.items():
        for a, b, _ in items:
            pairs[cls]["|".join(sorted({member_alloc[a], member_alloc[b]}))] += 1

    # Members outside the dominant allocation: how they attach (content-free).
    allocations = Counter(member_alloc)
    majority = allocations.most_common(1)[0][0] if allocations else None
    outsiders = [m for m in range(n) if member_alloc[m] != majority][:50]
    touching: dict[int, Counter[str]] = {m: Counter() for m in outsiders}
    partners: dict[int, Counter[str]] = {m: Counter() for m in outsiders}
    for cls, items in edges.items():
        for a, b, _ in items:
            for one, other in ((a, b), (b, a)):
                if one in touching:
                    touching[one][cls] += 1
                    partners[one][member_alloc[other]] += 1
    attachments = [
        {
            "allocation": member_alloc[m],
            "direct_hit": m in hit_members,
            "edges_by_class": dict(touching[m]),
            "partner_allocations": dict(partners[m]),
            "duplicate_group_size": len(dup_members[member_dup[m]]),
        }
        for m in outsiders
    ]

    # -- optional benchmark index: kind / token length of hit patterns ---------------------
    wanted_patterns = {*direct.values(), *focus_hits.values()}
    pattern_info: dict[bytes, dict[str, Any]] = {}
    if args.benchmark_index is not None and wanted_patterns:
        with args.benchmark_index.open("rb") as stream:
            for line in stream:
                entry = json.loads(line)
                identity = bytes.fromhex(canonical.digest(entry["tokens"]))
                if identity in wanted_patterns:
                    info = pattern_info.setdefault(
                        identity, {"token_length": len(entry["tokens"]), "kinds": Counter()}
                    )
                    for ref in entry.get("provenance", []):
                        kind = str(ref).rsplit(":", 1)[-1]
                        info["kinds"][kind if LABEL.match(kind) else "other"] += 1

    def pattern_summary(hits: dict[Any, bytes]) -> dict[str, Any]:
        per_pattern = Counter(hits.values())
        top = []
        for pattern, docs in per_pattern.most_common(args.top):
            item = {"fingerprint": fp(pattern), "documents": docs}
            if pattern in pattern_info:
                item["token_length"] = pattern_info[pattern]["token_length"]
                item["kinds"] = dict(pattern_info[pattern]["kinds"])
            top.append(item)
        return {
            "direct_hit_documents": len(hits),
            "distinct_patterns": len(per_pattern),
            "top_patterns": top,
            "documents_in_top_5_patterns": sum(d for _, d in per_pattern.most_common(5)),
        }

    focus_report: dict[str, Any] = {}
    for allocation in sorted(focus):
        selected = {w: v for w, v in focus_rows.items() if file_alloc.get(w[0]) == allocation}
        hits = {w: focus_hits[w] for w in selected if w in focus_hits}
        excluded = [w for w, v in selected.items() if v["decision"] == "excluded"]
        direct_excluded = [w for w in excluded if w in hits]
        family_sizes = Counter(
            "1"
            if selected[w]["family_size"] == 1
            else "2-10"
            if selected[w]["family_size"] <= 10
            else ">10"
            for w in excluded
        )
        size_buckets: dict[str, Counter[str]] = defaultdict(Counter)
        for w, v in selected.items():
            b = v["bytes"]
            bucket = (
                "<10KB"
                if b < 10_000
                else "10-100KB"
                if b < 100_000
                else ("100KB-1MB" if b < 1_000_000 else ">=1MB")
            )
            size_buckets[bucket]["documents"] += 1
            size_buckets[bucket]["direct_hits"] += int(w in hits)
        focus_report[allocation] = {
            "documents": len(selected),
            "decisions": dict(Counter(v["decision"] for v in selected.values())),
            "excluded_with_direct_hit": len(direct_excluded),
            "excluded_only_through_propagation": len(excluded) - len(direct_excluded),
            "excluded_family_size_buckets": dict(family_sizes),
            "distinct_excluded_families": len({selected[w]["family"] for w in excluded}),
            "direct_hit_rate_by_document_size": {
                k: dict(v) for k, v in sorted(size_buckets.items())
            },
            "hit_patterns": pattern_summary(hits),
        }

    body: dict[str, Any] = {
        "kind": "c05_component_forensics_v1",
        "content_free": True,
        "fingerprints": "HMAC-SHA256 with a per-run salt (or --salt-env), 16 hex",
        "plan_digest": plan.identity(),
        "plan_code_commit": plan.code_commit,
        "decisions_by_allocation": {k: dict(v) for k, v in sorted(by_allocation.items())},
        "direct_hit_documents_by_allocation": dict(sorted(all_hits_by_allocation.items())),
        "excluded_families": len(excluded_families),
        "target_component": {
            "rank": args.component_rank,
            "documents": n,
            "bytes": sum(member_bytes),
            "documents_by_allocation": dict(sorted(allocations.items())),
            "duplicate_groups": len(dup_members),
            "distinct_lineage_keys": len(postings),
            "lineage_keys_shared_by_2plus_members": sum(1 for p in postings if len(set(p)) > 1),
            "distinct_url_keys": sum(1 for c in key_class if c == "url"),
            "distinct_seed_url_keys": len(
                {k for (m, k), names in field_of.items() if "query_seed_url" in names}
            )
            if field_of
            else None,
            "graph_nodes": n,
            "graph_candidate_edges": sum(len(v) for v in edges.values()),
            "edges_by_class": {c: len(v) for c, v in sorted(edges.items())},
            "edges_by_allocation_pair": {c: dict(v) for c, v in sorted(pairs.items())},
            "lineage_key_posting_histogram": {c: dict(v) for c, v in sorted(histogram.items())},
            "direct_hit_documents": len(hit_members),
            "direct_hits_by_allocation": dict(Counter(member_alloc[m] for m in hit_members)),
            "excluded_only_through_propagation": n - len(hit_members),
            "hit_roots_before_bridging": len(hit_roots),
            "exclusion_counterfactuals": counterfactuals,
            "hit_patterns": pattern_summary(direct),
            "staged_union": stages,
            "each_class_alone": alone,
            "reconstruction_complete": reconstruction_complete,
            "dominant_keys": dominant,
            "single_key_removal_probes": probes,
            "non_majority_members": attachments,
        },
        "corpus_attribution": corpus or None,
        "focus_allocations": focus_report,
    }
    json.dump(body, sys.stdout, sort_keys=True, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
