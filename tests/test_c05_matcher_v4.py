"""c05-matcher-v4: exact whole-item fallback, lossless per-item dedup, content-free audit.

Authored synthetic rows only; no real benchmark payload, network or protected root.
"""

from __future__ import annotations

import getpass
import io
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import identity as identity_module
from xlm.data.exclusion.operator import main
from xlm.data.exclusion.policy import (
    C05Error,
    Informativeness,
    MatcherPolicy,
    MatcherPolicyV4,
    ProductionPolicy,
    Resources,
    matcher_policy,
)
from xlm.data.exclusion.prepare_workers import Progress
from xlm.data.exclusion.protected import INCOMPLETE_MARKER, MaterialSpec, audit, build
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.streaming import Pattern, composite, item_patterns, patterns, render

KEY = b"authored-matcher-v4-key-not-an-operator-key"
V4 = MatcherPolicyV4()
# Identities at HEAD 2f92922 (before v4): historical v3 meaning must not move.
V3_IDENTITY = "cc696e54b1fb8de2d42dafffa8a97de8e5f396c7cd61cf8e950d701624c93bb0"
PRODUCTION_V3_IDENTITY = "3a67baa3765a67a4f0b791dc4696ce5f375c468089507cb9d2bb9bcee5e6bd4f"

# Authored rows whose normal v3 signatures are empty but whose composite passes 4/16/3.
ARC_UNSIGNED = {"question": "Why is ice cold?", "choices": {"text": ["it melts", "heat"]}}
BLIMP_AT_FLOOR = {"sentence_good": "Cats ran.", "sentence_bad": "Cat ran."}  # 4 / 16 / 3
PIQA_UNSIGNED = {"goal": "Open a jar.", "sol1": "Twist lid.", "sol2": "Use teeth."}
HELLASWAG_UNSIGNED = {
    "ctx": "Man sits.",
    "endings": ["He waves.", "He nods."],
    "activity_label": "Run",
}
HELLASWAG_SPLIT = {"endings": ["He waves.", "He nods."], "activity_label": "Run"}
ARC_SIGNED = {
    "question": "Which granite ridge erodes fastest under persistent saffron winds?",
    "choices": {"text": ["the northern basalt harbor cliffs", "nothing"]},
}
LABELS = {"answerKey": "A", "label": 1, "gold": 0, "score": 0.5}


class GuardedRow(Mapping[str, Any]):
    """A row whose answer/label fields fail any access attempt."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.data = dict(data)

    def __getitem__(self, key: str) -> Any:
        if key in LABELS:
            raise AssertionError("label field accessed")
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("row iterated")

    def __len__(self) -> int:
        return len(self.data)


def run_item(task: str, row: Mapping[str, Any], policy: Any = V4) -> tuple[list[Pattern], bool]:
    _rendered, unique, _raw, fallback = item_patterns(task, row, "ref", policy)
    return unique, fallback


# --- versioning ------------------------------------------------------------------


def test_v3_identity_and_defaults_are_historical() -> None:
    assert MatcherPolicy().identity() == V3_IDENTITY
    assert ProductionPolicy().identity() == PRODUCTION_V3_IDENTITY
    assert MatcherPolicy().version == "c05-matcher-v3"
    assert "fallback" not in MatcherPolicy().model_dump()
    assert isinstance(matcher_policy({"version": "c05-matcher-v3"}), MatcherPolicy)
    with pytest.raises(ValueError):
        matcher_policy({})  # no implicit default version
    with pytest.raises(ValueError):
        MatcherPolicy.model_validate({"fallback": {"tokens": 4, "characters": 16, "distinct": 3}})


def test_v4_identity_binds_fallback_contract() -> None:
    assert (V4.version, V4.renderer) == ("c05-matcher-v4", "task-render-v3")
    assert (V4.fallback_renderer, V4.fallback_mode) == (
        "item-composite-v1",
        "exact-whole-item-only",
    )
    assert V4.fallback == Informativeness(tokens=4, characters=16, distinct=3)
    assert V4.identity() != V3_IDENTITY
    changed = V4.model_copy(
        update={"fallback": Informativeness(tokens=5, characters=16, distinct=3)}
    )
    assert changed.identity() != V4.identity()
    nested = ProductionPolicy.model_validate({"matcher": V4.model_dump(mode="json")})
    assert isinstance(nested.matcher, MatcherPolicyV4)
    assert nested.matcher.identity() == V4.identity()


# --- fallback semantics ------------------------------------------------------------


@pytest.mark.parametrize(
    "task,row",
    [
        ("arc_easy", ARC_UNSIGNED),
        ("blimp", BLIMP_AT_FLOOR),
        ("piqa", PIQA_UNSIGNED),
        ("hellaswag", HELLASWAG_UNSIGNED),
    ],
)
def test_unsigned_item_gets_exactly_one_exact_fallback(task: str, row: dict[str, Any]) -> None:
    assert run_item(task, row, MatcherPolicy()) == ([], False)  # v3 never falls back
    found, fallback = run_item(task, row)
    assert fallback and len(found) == 1
    from xlm.data.dedup.matchview import match_tokens

    assert found[0] == Pattern(tuple(match_tokens(composite(task, row))), ("ref:item_fallback",))


def test_v4_normal_item_is_exactly_v3() -> None:
    v3 = list(dict.fromkeys(patterns(render("arc_easy", ARC_SIGNED), "ref", MatcherPolicy())))
    found, fallback = run_item("arc_easy", ARC_SIGNED)
    assert v3 and found == v3 and not fallback
    assert all(not p.provenance[0].endswith(":item_fallback") for p in found)


def test_composite_contract_is_publisher_order_and_label_free() -> None:
    assert composite("arc_easy", ARC_UNSIGNED) == "Why is ice cold? it melts heat"
    assert composite("blimp", BLIMP_AT_FLOOR) == "Cats ran. Cat ran."
    assert composite("piqa", PIQA_UNSIGNED) == "Open a jar. Twist lid. Use teeth."
    assert composite("hellaswag", HELLASWAG_UNSIGNED) == "Man sits. He waves. He nods."
    split = {"ctx_a": "Man sits", "ctx_b": "down.", "endings": ["Waves.", "Nods."]}
    assert composite("hellaswag", split) == "Man sits down. Waves. Nods."


@pytest.mark.parametrize(
    "task,row",
    [
        ("arc_easy", ARC_UNSIGNED),
        ("piqa", PIQA_UNSIGNED),
        ("hellaswag", HELLASWAG_UNSIGNED),
        ("blimp", BLIMP_AT_FLOOR),
    ],
)
def test_fallback_never_reads_or_depends_on_labels(task: str, row: dict[str, Any]) -> None:
    expected = run_item(task, row)
    assert run_item(task, GuardedRow({**row, **LABELS})) == expected
    for value in (0, 1, 3, "B", "D"):
        assert run_item(task, {**row, "label": value, "answerKey": value}) == expected


def test_fallback_is_whole_item_never_sliding_windows() -> None:
    # 18 tokens, 4 distinct lexical tokens: every v3 floor fails, composite passes.
    row = {
        "question": "Is it so?",
        "choices": {"text": ["no no no", "so so so", "it is it", "is it so", "no it is"]},
    }
    assert run_item("arc_easy", row, MatcherPolicy()) == ([], False)
    found, fallback = run_item("arc_easy", row)
    assert fallback and len(found) == 1 and len(found[0].tokens) == 18 > V4.span_tokens


@pytest.mark.parametrize(
    "row,signed",
    [
        ({"sentence_good": "Cats ran.", "sentence_bad": "Cat ran."}, True),  # 4/16/3 exactly
        ({"sentence_good": "Cats ran.", "sentence_bad": "Cats ran."}, False),  # distinct 2
        ({"sentence_good": "Ox ate.", "sentence_bad": "Ax ate."}, False),  # 13 characters
        ({"sentence_good": "Elephants wandered.", "sentence_bad": "Zebras."}, False),  # 3 tokens
    ],
)
def test_fallback_floor_is_exactly_4_16_3(row: dict[str, Any], signed: bool) -> None:
    found, fallback = run_item("blimp", row)
    assert (bool(found), fallback) == (signed, signed)


# --- lossless dedup ----------------------------------------------------------------

# Synthetic HellaSwag-style row: ctx equals ctx_a + ctx_b up to punctuation, the
# activity-label and [title] preprocessing variants overlap the raw ones.
HELLASWAG_DUPLICATION = {
    "ctx": "A violet lantern drifts over the amber meadow, while cobalt ridge winds",
    "ctx_a": "A violet lantern drifts over the amber meadow while",
    "ctx_b": "cobalt ridge winds",
    "activity_label": "Lantern drifting",
    "endings": [
        "carry granite pebbles toward the saffron harbor [title] at dusk.",
        "carry granite pebbles toward the saffron harbor at dusk.",
        "fold the willow orchid into a quiet tundra fjord.",
    ],
    "label": 2,
}


def test_duplicate_token_provenance_emissions_are_removed() -> None:
    rendered, unique, raw, _ = item_patterns("hellaswag", HELLASWAG_DUPLICATION, "r", V4)
    emitted = list(patterns(rendered, "r", V4))
    assert raw == len(emitted) > len(unique) == len(set(emitted))
    assert set(unique) == set(emitted)


def test_same_tokens_with_different_provenance_are_retained() -> None:
    text = "the violet lantern drifts over the amber meadow tonight"
    found, _ = run_item("arc_easy", {"question": text, "choices": {"text": [text, "no"]}})
    kinds = {p.provenance[0] for p in found if " ".join(p.tokens) == text}
    assert kinds == {"ref:prompt", "ref:answer"}


def material(root: Path, files: dict[str, list[dict[str, Any]]], group: int = 0) -> MaterialSpec:
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for number, (name, rows) in enumerate(files.items()):
        task = name.split("-")[0]
        path = root / name
        if group:
            pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=group)
        else:
            path.write_bytes(b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows))
        entries.append(
            {
                "task": task,
                "repository": "authored/" + task,
                "revision": "1" * 40,
                "config": "authored-config",
                "split": f"split-{number}",
                "path": name,
                "sha256": file_sha(path),
                "bytes": path.stat().st_size,
                "items": len(rows),
                "format": "parquet" if group else "jsonl",
            }
        )
    return MaterialSpec.model_validate(
        {
            "files": entries,
            "publisher_inventory_sha256": "2" * 64,
            "all_published_configs_splits_reviewed": True,
            "isolation": {
                "mode": "authored",
                "operator_principal": "fixture-operator",
                "denied_agent_principal": "fixture-agent",
                "attestation_sha256": "3" * 64,
                "access_controls_verified": True,
            },
        }
    )


def prepare(
    spec: MaterialSpec, root: Path, out: Path, policy: Any, workers: int = 1, **kw: Any
) -> dict[str, Any]:
    identity = kw.pop("identity", ("4" * 40, "5" * 64, "6" * 64))
    return build(
        spec,
        root,
        out,
        policy=policy,
        resources=Resources(free_bytes=0, workers=workers, **kw),
        issuer="fixture",
        key=KEY,
        code_commit=identity[0],
        code_identity=identity[1],
        dependency_sha256=identity[2],
    )


def reference_index(spec: MaterialSpec, root: Path, policy: MatcherPolicy) -> bytes:
    """Independent pre-optimization semantics: raw v3 emissions, SQLite-style set."""
    lines: set[tuple[str, str, str]] = set()
    for entry in spec.files:
        rows = [canonical.loads_strict(x) for x in (root / entry.path).read_text().splitlines()]
        for number, row in enumerate(rows, 1):
            ref = canonical.digest([entry.model_dump(mode="json"), number])
            for p in patterns(render(entry.task, row), ref, policy):
                tokens = canonical.canonical_bytes(p.tokens).decode()
                lines.update((canonical.digest(p.tokens), r, tokens) for r in p.provenance)
    return b"".join(
        canonical.canonical_bytes({"tokens": canonical.loads_strict(tokens), "provenance": [ref]})
        + b"\n"
        for _hash, ref, tokens in sorted(lines)
    )


def test_v3_index_bytes_identical_to_pre_optimization_semantics(tmp_path: Path) -> None:
    rows = [HELLASWAG_DUPLICATION, HELLASWAG_DUPLICATION, {**HELLASWAG_DUPLICATION, "label": 0}]
    spec = material(tmp_path / "m", {"hellaswag-dup.jsonl": rows, "arc_easy-a.jsonl": [ARC_SIGNED]})
    envelope = prepare(spec, tmp_path / "m", tmp_path / "out", MatcherPolicy())
    index = (tmp_path / "out" / "index.jsonl").read_bytes()
    assert index == reference_index(spec, tmp_path / "m", MatcherPolicy())
    # Same tokens from three different items (identical/label-only-different rows) stay.
    per_item = len(item_patterns("hellaswag", HELLASWAG_DUPLICATION, "r", MatcherPolicy())[1])
    arc = len(item_patterns("arc_easy", ARC_SIGNED, "r", MatcherPolicy())[1])
    assert envelope["payload"]["patterns"] == index.count(b"\n") == 3 * per_item + arc
    assert envelope["payload"]["duplicate_items"] == 2


def mixed_rows() -> dict[str, list[dict[str, Any]]]:
    from c05_parallel_fixture import _row

    unsigned: dict[str, dict[str, Any]] = {
        "arc_easy": ARC_UNSIGNED,
        "blimp": BLIMP_AT_FLOOR,
        "piqa": PIQA_UNSIGNED,
        "hellaswag": {"ctx_a": "Man sits.", "ctx_b": "", **HELLASWAG_SPLIT},
    }
    return {
        f"{task}-{n}.parquet": [
            unsigned[task] if i % 5 == 0 else _row(task, n * 100 + i) for i in range(20)
        ]
        for n, task in enumerate(("arc_easy", "blimp", "piqa", "hellaswag"))
    }


def test_v4_workers_produce_identical_index_and_receipt(tmp_path: Path) -> None:
    spec = material(tmp_path / "m", mixed_rows(), group=3)
    outputs = set()
    for workers in (1, 2, 4, 16):
        out = tmp_path / f"w{workers}"
        envelope = prepare(spec, tmp_path / "m", out, V4, workers)
        assert envelope["payload"]["items_without_patterns"] == 0
        outputs.add(
            (
                (out / "index.jsonl").read_bytes(),
                (out / "benchmark-preparation.receipt.json").read_bytes(),
            )
        )
    assert len(outputs) == 1
    index = next(iter(outputs))[0]
    assert index.count(b":item_fallback") == 16


def test_protected_build_refuses_unsigned_items_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsigned = {"sentence_good": "Ox ate.", "sentence_bad": "Ax ate."}
    spec = material(tmp_path / "m", {"blimp-a.jsonl": [BLIMP_AT_FLOOR, unsigned]})
    isolation = spec.isolation.model_copy(
        update={"mode": "protected", "operator_principal": getpass.getuser()}
    )
    spec = spec.model_copy(update={"isolation": isolation})
    fixed = {"code_commit": "4" * 40, "code_identity": "5" * 64, "dependency_sha256": "6" * 64}
    monkeypatch.setattr(identity_module, "implementation_identity", lambda: fixed)
    out = tmp_path / "out"
    with pytest.raises(C05Error, match="without frozen signatures"):
        prepare(spec, tmp_path / "m", out, V4)
    assert (out / INCOMPLETE_MARKER).exists()
    assert not (out / "index.jsonl").exists()
    assert not (out / "benchmark-preparation.receipt.json").exists()
    # Authored mode keeps the historical fixture behavior (counted, not refused).
    authored = spec.model_copy(
        update={"isolation": spec.isolation.model_copy(update={"mode": "authored"})}
    )
    envelope = prepare(authored, tmp_path / "m", tmp_path / "authored", V4)
    assert envelope["payload"]["items_without_patterns"] == 1


# --- content-free audit ------------------------------------------------------------


def test_audit_counts_and_is_content_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = mixed_rows()
    files["blimp-1.parquet"][1] = {"sentence_good": "Ox ate.", "sentence_bad": "Ax ate."}
    root = tmp_path / "m"
    spec = material(root, files, group=3)
    before = sorted(p.name for p in root.iterdir())
    result = audit(spec, root, policy=V4, resources=Resources(workers=4))
    assert sorted(p.name for p in root.iterdir()) == before
    totals = result["totals"]
    assert totals["items"] == 80 and totals["files"] == 4
    assert totals["fallback_signed_items"] == 16 and totals["items_without_patterns"] == 1
    assert totals["normal_signed_items"] == 63
    assert (
        totals["raw_candidate_patterns"] - totals["emitted_patterns_after_lossless_dedup"]
        == (totals["deduplicated_pattern_emissions"])
    )
    assert result["unsigned_by_task_config_split"] == [
        {
            "task": "blimp",
            "config": "authored-config",
            "split": "split-1",
            "items_without_patterns": 1,
        }
    ]
    v3 = audit(spec, root, policy=MatcherPolicy(), resources=Resources(workers=1))
    assert v3["totals"]["fallback_signed_items"] == 0
    assert v3["totals"]["items_without_patterns"] == 17

    (tmp_path / "spec.json").write_text(json.dumps(spec.model_dump(mode="json")))
    (tmp_path / "policy.json").write_text(json.dumps(V4.model_dump(mode="json")))
    (tmp_path / "resources.json").write_text(json.dumps(Resources().model_dump(mode="json")))
    code = main(
        [
            "benchmark-audit-local",
            "--spec", str(tmp_path / "spec.json"),
            "--material-root", str(root),
            "--policy", str(tmp_path / "policy.json"),
            "--resources", str(tmp_path / "resources.json"),
            "--workers", "2",
        ]
    )  # fmt: skip
    captured = capsys.readouterr()
    assert code == 2  # one item remains unsigned
    stdout = json.loads(captured.out)
    assert stdout["totals"] == totals and stdout["workers"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ["m", "spec.json", "policy.json", "resources.json"]
    )
    # No benchmark text, tokens, signatures, provenance or row numbers anywhere.
    emitted = captured.out + captured.err
    words = {w.lower().strip(".?,") for rows in files.values() for r in rows for v in r.values()
             for w in (v if isinstance(v, str) else json.dumps(v)).split()}  # fmt: skip
    words = {w for w in words if len(w) >= 4 and not w.isdigit()}
    leaked = {w for w in words if w in emitted.lower()}
    assert leaked <= {"blimp", "piqa", "authored"}  # task/config names only
    for forbidden in (":item_fallback", ":prompt", "provenance", 'tokens"', 'row"'):
        assert forbidden not in emitted


def test_progress_reports_fallback_counts_without_content() -> None:
    stream = io.StringIO()
    progress = Progress(files=2, rows=10, workers=1, stream=stream)
    progress.update("audit", files=1, rows=5, patterns=7, fallback=3, force=True)
    line = stream.getvalue()
    assert "fallback items 3" in line and "patterns 7" in line
