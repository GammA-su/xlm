"""C06 frozen-policy tokenizer fit over authored C05 membership (synthetic only).

The module fixture builds one authored C05 completion through the production
operator path (generated corpus with planted excluded/duplicate records and
diagnostic_val/audit partitions). No real corpus, proof, tokenizer or network.
Size/count bounds are proven with scaled constructs, never by allocating them.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import c05_synthetic_flow as flow_module
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV, decide_and_plan, prepare, run_c05

from test_c05_detached_volume import actual, flow, proof_for  # noqa: F401 (fixtures)
from xlm.config.composer import load_yaml_str
from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import tokenizer_fit as fit_module
from xlm.data.exclusion.control import main
from xlm.data.exclusion.policy import C05Error, ProductionPolicy
from xlm.data.exclusion.progress import RunProgress
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selection import allocation_key, iter_plan_documents, tokenizer_identity
from xlm.data.exclusion.tokenizer_fit import (
    FIT_MANIFEST,
    FIT_SAMPLE,
    RESOURCE_PLAN,
    TOKENIZER_DIR,
    Budget,
    FitDeficit,
    FitPolicy,
    _Allocation,
    _Entry,
    apportion,
    fit_budgets,
    fit_tokenizer,
    is_oversized,
    load_fit_policy,
    plan_from_proof,
    verify_fit,
)
from xlm.data.exclusion.transport import open_gate
from xlm.tokenizers.bpe import ByteLevelBPETokenizer, FitSampleBoundError, _fit_text_stream

REPO = Path(__file__).resolve().parents[1]
PRODUCTION_POLICY = REPO / "recipes/tokenizer/mix01_fit_shares_v1.yaml"
QUOTAS = REPO / "recipes/mixtures/mix01_quotas_6b.yaml"
TARGET = 11 * 4096
VOCAB = flow_module.VOCAB
EQUAL = dict.fromkeys(
    (
        "essential_science",
        "essential_practical",
        "essential_prose",
        "ultrax_ultrafineweb",
        "finepdfs_en",
        "synth_en_explanations",
        "nemotron_wiki_rewrite",
        "finewiki_en",
        "ifm_behaviors_general_planning",
        "common_pile_prose",
        "simple_stories",
    ),
    1,
)
load = canonical.loads_bytes_strict


@pytest.fixture(scope="module")
def c05(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """One authored C05 completion with kept train, diagnostic_val and audit rows."""
    environment = pytest.MonkeyPatch()
    environment.setenv(KEY_ENV, KEY)  # Module-scoped fixtures run before ``key``.
    monkey = pytest.MonkeyPatch()
    # The generated flow freezes audit_bytes=0; enable a real audit partition here.
    monkey.setattr(
        flow_module,
        "ProductionPolicy",
        lambda **_: ProductionPolicy(diagnostic_bytes=4096, quick_bytes=0, audit_bytes=4096),
    )
    root = tmp_path_factory.mktemp("c06") / "root"
    paths = prepare(root)
    plan_path = decide_and_plan(paths)
    result = run_c05(paths, plan_path)
    monkey.undo()
    yield {
        "root": root,
        "proof": result["proof"],
        "quotas": root / "quotas.yaml",
        "ifm": root / "ifm-split.json",
        "completion": result["completion"]["payload"],
    }
    environment.undo()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


def policy_body() -> dict[str, Any]:
    return load_yaml_str(PRODUCTION_POLICY.read_text(encoding="utf-8"))


def write_policy(path: Path, c05: dict[str, Any], **changes: Any) -> Path:
    """Development policy for the authored corpus; ``changes`` override any field."""
    body = policy_body()
    body.update(
        mode="development",
        policy_id="authored_fit_v1",
        target_sample_bytes=TARGET,
        max_document_bytes=100_000,
        seed=7,
    )
    body["tokenizer"]["target_vocab_size"] = VOCAB
    body["internal_splits"]["quotas_sha256"] = file_sha(c05["quotas"])
    body["internal_splits"]["quotas_path"] = "quotas.yaml"
    body.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def fit(
    c05: dict[str, Any],
    policy_path: Path,
    out: Path,
    progress: RunProgress | None = None,
) -> dict[str, Any]:
    policy, sha = load_fit_policy(policy_path)
    planned = plan_from_proof(c05["proof"], policy, c05["quotas"], c05["ifm"])
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        return fit_tokenizer(
            gate,
            policy,
            sha,
            quotas=c05["quotas"],
            ifm_split=c05["ifm"],
            scratch=out / "scratch",
            output=out / "fit",
            issuer=ISSUER,
            key=KEY.encode(),
            accepted_plan_digest=planned["digest"],
            progress=progress,
            heartbeat_seconds=0.05,
        )


@pytest.fixture(scope="module")
def fitted(c05: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("fitted")
    policy = write_policy(out / "policy.yaml", c05)
    stream = io.StringIO()
    progress = RunProgress(interval=0.001, stream=stream, label="C06", min_span=0.001)
    envelope = fit(c05, policy, out, progress)
    return {
        "out": out,
        "policy": policy,
        "fit": out / "fit",
        "envelope": envelope,
        "body": envelope["payload"],
        "progress": stream.getvalue(),
    }


def sample_rows(directory: Path) -> list[dict[str, Any]]:
    return [load(line) for line in (directory / FIT_SAMPLE).read_bytes().splitlines()]


def plan_documents(c05: dict[str, Any]) -> list[tuple[str, CanonicalDocument]]:
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        return [
            (allocation_key(i.component, i.view, i.upstream_component), d)
            for i, d in iter_plan_documents(gate)
        ]


def membership(c05: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """doc_id -> (decision, split) for kept rows; absent IDs are excluded/duplicate."""
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        rows = gate.db.execute("SELECT id,decision,split FROM membership").fetchall()
    return {doc_id: (decision, split) for doc_id, decision, split in rows}


# -- end to end ------------------------------------------------------------------------


def test_end_to_end_layout_binding_and_identity(
    c05: dict[str, Any], fitted: dict[str, Any]
) -> None:
    out = fitted["fit"]
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [TOKENIZER_DIR, FIT_MANIFEST, FIT_SAMPLE, RESOURCE_PLAN]
    )
    assert sorted(p.name for p in (out / TOKENIZER_DIR).iterdir()) == [
        "c05-binding.json",
        "tokenizer.json",
        "tokenizer_manifest.json",
    ]
    assert not list(fitted["out"].glob("fit.partial-*"))
    assert not list((fitted["out"] / "scratch").glob("c06-fit-*"))
    body = fitted["body"]
    binding = json.loads((out / TOKENIZER_DIR / "c05-binding.json").read_text(encoding="utf-8"))
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        tokenizer, identity = tokenizer_identity(out / TOKENIZER_DIR, gate)
        assert binding == {
            "plan_digest": gate.plan_digest,
            "completion_digest": gate.receipt_digest,
            "tokenizer_fingerprint": tokenizer.fingerprint,
        }
        assert identity["c05_fit_binding"] and identity["vocab_size"] == VOCAB
        assert body["tokenizer"]["fingerprint"] == tokenizer.fingerprint
        assert body["mode"] == "authored" and body["production"] is False
        result = verify_fit(
            gate, out, load_fit_policy(fitted["policy"])[0], c05["quotas"], c05["ifm"]
        )
    assert result["verified"] and result["fit_digest"] == fitted["envelope"]["digest"]


def test_progress_is_staged_and_content_free(c05: dict[str, Any], fitted: dict[str, Any]) -> None:
    text = fitted["progress"]
    lines = text.splitlines()
    assert lines and all(line.startswith("[C06] ") for line in lines)
    for stage in (
        "SAMPLE INDEX",
        "SAMPLE SELECT",
        "SAMPLE VERIFY",
        "TOKENIZER FIT",
        "TOKENIZER FIT: MERGES",
        "TOKENIZER SAVE",
        "VERIFY",
        "COMPLETE",
    ):
        assert f"[C06] {stage} |" in text, stage
    assert "merge progress is not observable" in text
    assert "ETA" in text and "elapsed" in text and "%" in text and "GiB input" in text
    # No IDs, digests, record text or paths: no long hex runs, no generated words.
    assert not re.search(r"[0-9a-f]{16}", text)
    assert not re.search(r"\bw[0-9a-f]{7}\b", text)
    assert str(c05["root"]) not in text and ":\\" not in text


# -- C05 eligibility -------------------------------------------------------------------


def test_only_exact_kept_train_records_are_offered_and_sampled(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered: list[str] = []
    original = _Allocation.offer

    def spy(self: _Allocation, entry: _Entry) -> int:
        offered.append(entry.doc_id)
        return original(self, entry)

    monkeypatch.setattr(_Allocation, "offer", spy)
    envelope = fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    kept = membership(c05)
    documents = plan_documents(c05)
    non_kept = {d.doc_id for _, d in documents if d.doc_id not in kept}
    diagnostic = {i for i, (_, s) in kept.items() if s == "diagnostic_val"}
    audit = {i for i, (_, s) in kept.items() if s == "audit"}
    train = {i for i, (decision, s) in kept.items() if (decision, s) == ("kept", "train")}
    # Planted: one excluded and one duplicate per allocation; real diag/audit partitions.
    assert len(non_kept) == 34 and diagnostic and audit
    assert set(offered) == train and len(offered) == len(train)
    sample = {row["doc_id"] for row in sample_rows(tmp_path / "fit")}
    assert sample and sample <= train
    assert not sample & (non_kept | diagnostic | audit)
    assert envelope["payload"]["totals"]["selected_documents"] == len(sample)


def _replace_text(doc: CanonicalDocument, text: str) -> CanonicalDocument:
    from dataclasses import replace

    return replace(doc, text=text, utf8_byte_count=len(text.encode()))


def test_content_mismatch_refuses(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = membership(c05)
    original = fit_module.iter_plan_documents

    def changed(gate: Any, **kwargs: Any) -> Iterator[Any]:
        done = False
        for item, doc in original(gate, **kwargs):
            if not done and kept.get(doc.doc_id) == ("kept", "train"):
                done = True
                doc = _replace_text(doc, doc.text + " changed")
            yield item, doc

    monkeypatch.setattr(fit_module, "iter_plan_documents", changed)
    with pytest.raises(C05Error, match="differs from C05 kept membership"):
        fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))


def test_unknown_record_refuses(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    original = fit_module.iter_plan_documents

    def extra(gate: Any, **kwargs: Any) -> Iterator[Any]:
        last = None
        for item, doc in original(gate, **kwargs):
            last = item, doc
            yield item, doc
        assert last is not None
        yield last[0], replace(last[1], doc_id="0" * 64)  # Never screened by C05.

    monkeypatch.setattr(fit_module, "iter_plan_documents", extra)
    with pytest.raises(C05Error, match="completion accounting"):
        fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert not (tmp_path / "fit").exists()


def test_second_pass_change_refuses_before_any_fit(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fit_module.iter_plan_documents
    calls = {"n": 0}

    def changed_later(gate: Any, **kwargs: Any) -> Iterator[Any]:
        calls["n"] += 1
        for item, doc in original(gate, **kwargs):
            if calls["n"] == 2:
                doc = _replace_text(doc, doc.text + " changed")
            yield item, doc

    def consume(cls: Any, documents: Any, **kwargs: Any) -> Any:
        for _ in documents:
            pass
        pytest.fail("a changed second pass must refuse before the trainer finishes")

    monkeypatch.setattr(ByteLevelBPETokenizer, "train_from_documents", classmethod(consume))
    monkeypatch.setattr(fit_module, "iter_plan_documents", changed_later)
    with pytest.raises(C05Error, match="pass-1 C05 binding"):
        fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert not (tmp_path / "fit").exists() and not list((tmp_path / "scratch").glob("c06-*"))


def test_changed_input_bytes_refuse(c05: dict[str, Any], tmp_path: Path) -> None:
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        path = Path(gate.plan.data_root) / gate.plan.files[0].path
    original = path.read_bytes()
    position = original.index(b'"text":"w') + len(b'"text":"w')
    replacement = b"x" if original[position : position + 1] != b"x" else b"y"
    try:
        path.write_bytes(original[:position] + replacement + original[position + 1 :])
        with pytest.raises(C05Error):
            fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    finally:
        path.write_bytes(original)
    assert not (tmp_path / "fit").exists()


def test_changed_c05_proof_refuses(
    c05: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proof = load(c05["proof"].read_bytes())
    proof["completion_digest"] = "1" * 64
    changed = tmp_path / "proof.json"
    canonical.write_canonical_json(changed, proof)
    args = cli_args({**c05, "proof": changed}, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert main([*args, "--plan-only", "--no-progress"]) == 0
    digest = json.loads(capsys.readouterr().out)["resource_plan_digest"]
    assert main([*args, *signing(digest), "--no-progress"]) == 1
    assert "C05 completion changed" in capsys.readouterr().out
    assert not (tmp_path / "fit").exists()


# -- policy ----------------------------------------------------------------------------


def test_production_policy_is_the_frozen_operator_decision() -> None:
    policy, sha = load_fit_policy(PRODUCTION_POLICY)
    assert policy.mode == "production" and policy.policy_id == "mix01_fit_shares_v1"
    assert policy.component_weights == EQUAL
    assert all(type(w) is int for w in policy.component_weights.values())
    assert (
        policy.target_sample_bytes,
        policy.tokenizer.target_vocab_size,
        policy.seed,
        policy.max_document_bytes,
    ) == (536_870_912, 32_768, 20_260_919, 1_048_576)
    assert policy.tokenizer.type == "byte_level_bpe"
    assert policy.internal_splits.quotas_sha256 == file_sha(QUOTAS)
    assert policy.internal_splits.quotas_path == "recipes/mixtures/mix01_quotas_6b.yaml"
    assert policy.internal_splits.composite == {
        "ifm_behaviors_general_planning": "ifm_requirement_split",
        "common_pile_prose": "common_pile_component_split",
    }
    rationale = " ".join(policy.rationale)
    for reason in ("Natural corpus bytes are rejected", "M0 final training weights are rejected"):
        assert reason in rationale
    assert sha == hashlib.sha256(PRODUCTION_POLICY.read_bytes()).hexdigest()
    assert len(policy.identity()) == 64


_DELETE = object()


def _mutate(body: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    target = body
    for name in path[:-1]:
        target = target[name]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return body


PRODUCTION_REFUSALS = [
    (("component_weights", "simple_stories"), _DELETE),
    (("component_weights", "txt360_web"), 1),
    (("component_weights", "finewiki_en"), 0),
    (("component_weights", "finewiki_en"), -1),
    (("component_weights", "finewiki_en"), True),
    (("component_weights", "finewiki_en"), 1.0),
    (("component_weights", "finewiki_en"), "1"),
    (("target_sample_bytes",), 536_870_911),
    (("tokenizer", "target_vocab_size"), 32_000),
    (("tokenizer", "special_tokens"), ["<pad>", "<bos>", "<eos>"]),
    (("seed",), 20_260_918),
    (("max_document_bytes",), 2 * 1_048_576),
    (("rules", "crossing"), "truncate_crossing_document"),
    (("rules", "shortfall"), "redistribute"),
    (("rules", "oversized"), "truncate"),
    (("rules", "order"), "file_order"),
    (("rules", "unknown"), "x"),
    (("unknown_field",), 1),
    (("internal_splits",), _DELETE),
    (("internal_splits", "composite", "common_pile_prose"), _DELETE),
    (("internal_splits", "composite", "ifm_behaviors_general_planning"), "equal_split"),
    (("internal_splits", "quotas_sha256"), "not-a-digest"),
    (("component_weights",), _DELETE),
    (("mode",), "fallback"),
]


@pytest.mark.parametrize(("path", "value"), PRODUCTION_REFUSALS)
def test_policy_parser_fails_closed(path: tuple[str, ...], value: Any, tmp_path: Path) -> None:
    body = _mutate(policy_body(), path, value)
    target = tmp_path / "policy.yaml"
    target.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fit_policy(target)


def test_policy_parser_refuses_duplicate_keys(tmp_path: Path) -> None:
    raw = PRODUCTION_POLICY.read_text(encoding="utf-8")
    target = tmp_path / "policy.yaml"
    target.write_text(
        raw.replace("  finewiki_en: 1\n", "  finewiki_en: 1\n  finewiki_en: 2\n"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_fit_policy(target)


def test_policy_identity_changes_with_every_rule() -> None:
    development = policy_body() | {"mode": "development"}
    identity = FitPolicy.model_validate(development).identity()
    seen = {identity}
    for path, value in [
        (("target_sample_bytes",), 1024),
        (("seed",), 1),
        (("max_document_bytes",), 512),
        (("tokenizer", "target_vocab_size"), 512),
        (("component_weights", "finewiki_en"), 2),
        (("internal_splits", "quotas_sha256"), "0" * 64),
        (("policy_id",), "other"),
        (("rationale",), ["changed"]),
    ]:
        body = _mutate(policy_body() | {"mode": "development"}, path, value)
        seen.add(FitPolicy.model_validate(body).identity())
    assert len(seen) == 9
    # Production rules are single-valued literals: a changed rule cannot even parse.
    assert FitPolicy.model_validate(policy_body()).identity() != identity


def test_development_policy_cannot_serve_protected_membership(tmp_path: Path) -> None:
    from types import SimpleNamespace

    policy = FitPolicy.model_validate(policy_body() | {"mode": "development"})
    with pytest.raises(C05Error, match="requires the production fit policy"):
        fit_tokenizer(
            SimpleNamespace(mode="protected"),  # type: ignore[arg-type]
            policy,
            "0" * 64,
            quotas=QUOTAS,
            ifm_split=tmp_path / "unused.json",
            scratch=tmp_path / "scratch",
            output=tmp_path / "fit",
            issuer=ISSUER,
            key=KEY.encode(),
            accepted_plan_digest="0" * 64,
        )
    assert not (tmp_path / "scratch").exists()


def test_production_policy_refuses_a_different_quota_table(c05: dict[str, Any]) -> None:
    # The production policy pins the real Mix-01 quota bytes; authored quotas fail closed.
    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    with pytest.raises(C05Error, match="quota table differs"):
        plan_from_proof(c05["proof"], policy, c05["quotas"], c05["ifm"])


# -- budgets ---------------------------------------------------------------------------


def mix01_allocations(**overrides: int) -> dict[str, int]:
    """Allocation quotas shaped like frozen_requirements output for Mix-01."""
    finals = dict(flow_module.FINALS) | overrides
    result: dict[str, int] = {}
    for component, quota in finals.items():
        if component == "ifm_behaviors_general_planning":
            for view, part in flow_module.IFM_VIEWS.items():
                result[allocation_key(component, view, None)] = part
        elif component == "common_pile_prose":
            for upstream, part in flow_module.COMMON_PILE.items():
                result[allocation_key(component, component, upstream)] = part
        else:
            result[allocation_key(component, component, None)] = quota
    return result


def by_component(budgets: dict[str, Budget]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for budget in budgets.values():
        totals[budget.component] = totals.get(budget.component, 0) + budget.requested
    return totals


def test_production_budget_derivation_is_exact() -> None:
    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    budgets = fit_budgets(policy, mix01_allocations())
    # 536,870,912 = 11 * 48,806,446 + 6: the 6 alphabetically first components get +1.
    first = set(sorted(EQUAL)[:6])
    assert by_component(budgets) == {c: 48_806_447 if c in first else 48_806_446 for c in EQUAL}
    assert sum(b.requested for b in budgets.values()) == 536_870_912
    ifm = [b.requested for b in budgets.values() if b.component.startswith("ifm")]
    assert sorted(ifm) == [24_403_223, 24_403_223]  # 48,806,446 / 2 for equal quotas.
    common = {
        json.loads(k)[2]: b.requested
        for k, b in budgets.items()
        if b.component == "common_pile_prose"
    }
    assert common == apportion(48_806_447, flow_module.COMMON_PILE)
    assert all(b.declared_share.denominator % 11 == 0 for b in budgets.values())
    single = budgets[allocation_key("simple_stories", "simple_stories", None)]
    assert str(single.declared_share) == "1/11"


def test_m0_weights_and_natural_availability_do_not_change_budgets(
    fitted: dict[str, Any],
) -> None:
    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    base = fit_budgets(policy, mix01_allocations())
    # Non-composite quotas carry the M0 weights: any change leaves every budget unchanged.
    skewed = mix01_allocations(essential_science=9_999, simple_stories=1, ultrax_ultrafineweb=7)
    assert {k: b.requested for k, b in fit_budgets(policy, skewed).items()} == {
        k: b.requested for k, b in base.items()
    }
    # End to end: availability differs >4x across components; requests are equal.
    rows = fitted["body"]["components"]
    assert {r["requested_bytes"] for r in rows.values()} == {4096}
    available = [r["available_eligible_bytes"] for r in rows.values()]
    assert max(available) > 4 * min(available)
    assert {r["declared_share"] for r in rows.values()} == {"1/11"}


def test_frozen_internal_splits_divide_only_their_component(fitted: dict[str, Any]) -> None:
    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    uneven = mix01_allocations()
    general = allocation_key("ifm_behaviors_general_planning", "general", None)
    planning = allocation_key("ifm_behaviors_general_planning", "planning", None)
    uneven[general], uneven[planning] = 100, 200
    budgets = fit_budgets(policy, uneven)
    assert (budgets[general].requested, budgets[planning].requested) == (16_268_815, 32_537_631)
    assert by_component(budgets) == by_component(fit_budgets(policy, mix01_allocations()))
    # End to end, from the authored frozen IFM split and Common Pile split.
    rows = fitted["body"]["allocations"]
    ifm = {
        json.loads(k)[1]: r["requested_bytes"]
        for k, r in rows.items()
        if r["component"].startswith("ifm")
    }
    assert ifm == apportion(4096, flow_module.IFM_VIEWS)
    common = {
        json.loads(k)[2]: r["requested_bytes"]
        for k, r in rows.items()
        if r["component"] == "common_pile_prose"
    }
    assert common == apportion(4096, flow_module.COMMON_PILE)
    requirements = fitted["body"]["requirements"]
    assert len(requirements["ifm_split_digest"]) == 64
    assert len(requirements["common_pile_split_digest"]) == 64


def test_no_equal_share_fallback(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.pools import tokenizer_fit as pools

    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    missing = mix01_allocations()
    del missing[allocation_key("simple_stories", "simple_stories", None)]
    with pytest.raises(C05Error, match="cover exactly"):
        fit_budgets(policy, missing)
    with pytest.raises(C05Error):
        apportion(10, {})
    # The P11 helper (with its silent equal-share fallback) is never on this path.
    monkeypatch.setattr(pools, "build_tokenizer_fit_manifest", lambda *a, **k: pytest.fail("P11"))
    envelope = fit(c05, write_policy(tmp_path / "p.yaml", c05, seed=11), tmp_path)
    assert envelope["payload"]["policy"]["component_weights"] == EQUAL


# -- sampling semantics ----------------------------------------------------------------


def _brute_prefix(entries: list[_Entry], budget: int) -> list[str]:
    taken, total = [], 0
    for entry in sorted(entries, key=_Entry.key):
        if total >= budget:
            break
        taken.append(entry.doc_id)
        total += entry.size
    return taken


@pytest.mark.parametrize("budget", [0, 1, 250, 5_000, 10**9])
def test_prefix_is_order_independent_with_whole_crossing_document(budget: int) -> None:
    rng = random.Random(budget)
    entries = [
        _Entry(rng.randbytes(4), f"d{i:05d}", "c", rng.choice([0, 1, 7, 100, 999]))
        for i in range(2_000)
    ]
    entries.append(_Entry(entries[0].rank, "d99999", "c", 5))  # Rank tie: doc_id decides.
    expected = _brute_prefix(entries, budget)
    for permutation in range(4):
        rng.shuffle(entries)
        state = _Allocation(budget)
        for entry in entries:
            state.offer(entry)
        held = sorted(state.heap, key=_Entry.key)
        assert [e.doc_id for e in held] == expected, permutation
        assert state.held == sum(e.size for e in held)
        if sum(e.size for e in entries) >= budget:
            assert state.held >= budget  # The crossing document is whole...
            assert not held or state.held - held[-1].size < budget  # ...and it is the last.


def test_prefix_retention_is_bounded_by_the_budget() -> None:
    state = _Allocation(1_000)
    peak = 0
    for i in range(20_000):
        state.offer(_Entry(hashlib.sha256(str(i).encode()).digest(), str(i), "c", 100))
        peak = max(peak, len(state.heap))
    assert len(state.heap) == 10 and peak <= 11


def test_crossing_overshoot_and_sample_accounting(fitted: dict[str, Any]) -> None:
    body, rows = fitted["body"], sample_rows(fitted["fit"])
    policy = load_fit_policy(fitted["policy"])[0]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(allocation_key(*row["allocation"]), []).append(row)
    for key, report in body["allocations"].items():
        members = sorted(grouped[key], key=lambda r: (r["rank"], r["doc_id"]))
        sizes = [r["bytes"] for r in members]
        assert sum(sizes) == report["selected_bytes"] and len(sizes) == report["selected_documents"]
        assert sum(sizes[:-1]) < report["requested_bytes"] <= sum(sizes)
        assert report["overshoot_bytes"] == sum(sizes) - report["requested_bytes"]
        assert 0 <= report["overshoot_bytes"] < policy.max_document_bytes
        ranks = [
            fit_module._rank(policy.seed, key, r["doc_id"], r["content"]).hex() for r in members
        ]
        assert ranks == [r["rank"] for r in members]
    totals = body["totals"]
    assert totals["selected_bytes"] == sum(r["bytes"] for r in rows)
    assert totals["selected_bytes"] == body["sample"]["canonical_bytes"]
    assert totals["overshoot_bytes"] == totals["selected_bytes"] - TARGET
    assert totals["selected_documents"] == len(rows) == body["sample"]["documents"]
    assert sum(r["selected_bytes"] for r in body["components"].values()) == totals["selected_bytes"]
    assert [r["doc_id"] for r in rows] == sorted(r["doc_id"] for r in rows)
    assert all(set(r) == {"doc_id", "content", "allocation", "bytes", "rank"} for r in rows)


def test_fit_is_deterministic_and_seed_bound(
    c05: dict[str, Any], fitted: dict[str, Any], tmp_path: Path
) -> None:
    again = fit(c05, fitted["policy"], tmp_path / "again")
    assert again["digest"] == fitted["envelope"]["digest"]
    for name in (
        FIT_SAMPLE,
        f"{TOKENIZER_DIR}/tokenizer.json",
        f"{TOKENIZER_DIR}/c05-binding.json",
    ):
        assert (tmp_path / "again/fit" / name).read_bytes() == (fitted["fit"] / name).read_bytes()
    other = fit(c05, write_policy(tmp_path / "seed.yaml", c05, seed=8), tmp_path / "seed")
    assert {r["doc_id"] for r in sample_rows(tmp_path / "seed/fit")} != {
        r["doc_id"] for r in sample_rows(fitted["fit"])
    }
    assert other["payload"]["policy_digest"] != fitted["body"]["policy_digest"]
    changed_policy = load_fit_policy(tmp_path / "seed.yaml")[0]
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        with pytest.raises(C05Error, match="different fit policy"):
            verify_fit(gate, fitted["fit"], changed_policy, c05["quotas"], c05["ifm"])


# -- the complete frozen sample reaches BPE --------------------------------------------


def test_complete_sample_reaches_bpe_even_with_scaled_down_defaults(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scaled stand-in for >100k docs / >500 MiB: shrink the historical defaults to 2 docs
    and 100 bytes, far below the sample. The bridge must still feed every selected record."""
    from xlm.tokenizers import bpe

    function = ByteLevelBPETokenizer.train_from_documents.__func__  # type: ignore[attr-defined]
    monkeypatch.setattr(function, "__defaults__", (32768, 2, 100, False, None))
    calls: list[dict[str, Any]] = []
    original = bpe._fit_text_stream

    def spy(documents: Any, max_docs: int, max_bytes: int, **kwargs: Any) -> Any:
        calls.append({"max_docs": max_docs, "max_bytes": max_bytes, **kwargs})
        return original(documents, max_docs, max_bytes, **kwargs)

    monkeypatch.setattr(bpe, "_fit_text_stream", spy)
    # Control: a default caller is now capped at 2 documents.
    from test_performance_tokenization import document

    train = [document("ab", i) for i in range(5)]
    capped = ByteLevelBPETokenizer.train_from_documents(train, target_vocab_size=VOCAB)
    assert calls[-1]["max_docs"] == 2 and calls[-1]["require_complete"] is False
    envelope = fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    body = envelope["payload"]
    sample = body["sample"]
    assert sample["documents"] > 2 and sample["canonical_bytes"] > 100
    assert calls[-1]["max_docs"] == sample["documents"]
    assert calls[-1]["max_bytes"] == sample["canonical_bytes"]
    assert calls[-1]["require_complete"] is True
    assert body["bpe_bounds"]["fed_documents"] == sample["documents"]
    assert body["bpe_bounds"]["fed_canonical_bytes"] == sample["canonical_bytes"]
    # Independent recomputation: plan-order hash over exactly the selected records.
    selected = {r["doc_id"] for r in sample_rows(tmp_path / "fit")}
    expected = hashlib.sha256(
        "\n".join(
            f"{d.source_id}:{d.doc_id}:{d.clean_hash}"
            for _, d in plan_documents(c05)
            if d.doc_id in selected
        ).encode()
    ).hexdigest()
    manifest_path = tmp_path / "fit" / TOKENIZER_DIR / "tokenizer_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["training_input_hash"] == expected == sample["training_input_hash"]
    assert capped.fingerprint != body["tokenizer"]["fingerprint"]


def test_strict_stream_refuses_instead_of_truncating() -> None:
    from test_performance_tokenization import document

    docs = [document(f"passage {i} " * 3, i) for i in range(5)]
    total = sum(d.utf8_byte_count for d in docs)
    with _fit_text_stream(iter(docs), len(docs), total, require_complete=True) as (texts, _):
        assert len(list(texts)) == len(docs)
    for max_docs, max_bytes in ((len(docs) - 1, total), (len(docs), total - 1)):
        with pytest.raises(FitSampleBoundError):
            with _fit_text_stream(iter(docs), max_docs, max_bytes, require_complete=True):
                pytest.fail("a truncated sample must never reach the trainer")


def test_logical_scale_bounds_are_passed_through_uncapped() -> None:
    """Logical >100k-document / >512 MiB bounds (no allocation): nothing clamps them to
    the historical 100k / 500 MiB defaults, so every frozen record would be fed."""
    from test_performance_tokenization import document

    count, size = 150_000, 600 * 1024**2
    seen: list[tuple[int, int]] = []
    docs = [document("x", i) for i in range(3)]
    with _fit_text_stream(
        iter(docs), count, size, require_complete=True, on_feed=lambda d, b: seen.append((d, b))
    ) as (texts, _):
        assert len(list(texts)) == 3
    assert seen[-1] == (3, 3) and count > 100_000 and size > 500 * 1024**2


# -- document cap and shortfall --------------------------------------------------------


def test_one_mib_cap_boundary_on_the_production_policy() -> None:
    policy = load_fit_policy(PRODUCTION_POLICY)[0]
    assert not is_oversized(policy, 1_048_576)
    assert is_oversized(policy, 1_048_577)


def test_oversized_documents_are_skipped_never_truncated(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = 620  # Scaled stand-in for the 1 MiB production cap.
    fed: list[CanonicalDocument] = []
    original = fit_module.materialize

    def spy(*args: Any, **kwargs: Any) -> Iterator[CanonicalDocument]:
        for doc in original(*args, **kwargs):
            fed.append(doc)
            yield doc

    monkeypatch.setattr(fit_module, "materialize", spy)
    policy = write_policy(
        tmp_path / "p.yaml", c05, max_document_bytes=cap, target_sample_bytes=11 * 1024
    )
    body = fit(c05, policy, tmp_path)["payload"]
    totals = body["totals"]
    assert totals["oversized_candidates_skipped"] > 0 and totals["oversized_candidate_bytes"] > cap
    assert fed and all(d.utf8_byte_count <= cap for d in fed)
    assert all(r["bytes"] <= cap for r in sample_rows(tmp_path / "fit"))
    by_id = {d.doc_id: d for _, d in plan_documents(c05)}
    assert all(d.text == by_id[d.doc_id].text for d in fed)  # Fed verbatim, never cut.
    skipped = sum(r["oversized_candidates_skipped"] for r in body["allocations"].values())
    assert skipped == totals["oversized_candidates_skipped"]


def test_shortfall_refuses_without_redistribution(
    c05: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from xlm.data.exclusion.quotas import frozen_requirements

    # 8 KiB per component exceeds simple_stories' eligible bytes; others have enough.
    policy_path = write_policy(tmp_path / "p.yaml", c05, target_sample_bytes=11 * 8192)
    args = cli_args(c05, policy_path, tmp_path)
    assert main([*args, "--plan-only", "--no-progress"]) == 0
    digest = json.loads(capsys.readouterr().out)["resource_plan_digest"]
    assert main([*args, *signing(digest), "--no-progress"]) == 2
    report = load((tmp_path / "deficit.json").read_bytes())
    rows = report["allocations"]
    deficient = {k for k, r in rows.items() if r["status"] == "DEFICIT"}
    assert deficient == {allocation_key("simple_stories", "default", None)}
    policy = load_fit_policy(policy_path)[0]
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        requirements = frozen_requirements(gate.input_manifest, c05["quotas"], c05["ifm"])
    budgets = fit_budgets(policy, requirements["allocations"])
    assert {k: r["requested_bytes"] for k, r in rows.items()} == {
        k: b.requested for k, b in budgets.items()
    }
    for row in rows.values():
        assert row["deficit_bytes"] == max(
            0, row["requested_bytes"] - row["available_eligible_bytes"]
        )
    assert "no redistribution" in report["rule"]
    _assert_content_free(c05, (tmp_path / "deficit.json").read_text(encoding="utf-8"))
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))
    assert not list((tmp_path / "scratch").glob("c06-fit-*"))


def test_shortfall_caused_by_the_document_cap_refuses(c05: dict[str, Any], tmp_path: Path) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05, max_document_bytes=560)
    with pytest.raises(FitDeficit) as caught:
        fit(c05, policy, tmp_path)
    rows = caught.value.report["allocations"]
    short = [r for r in rows.values() if r["status"] == "DEFICIT"]
    assert short and all(r["oversized_candidates_skipped"] > 0 for r in short)
    assert all(r["available_eligible_bytes"] < r["requested_bytes"] for r in short)
    assert not (tmp_path / "fit").exists()


def _assert_content_free(c05: dict[str, Any], text: str) -> None:
    for _, doc in plan_documents(c05):
        assert doc.doc_id not in text and doc.text[:40] not in text


# -- bounded memory, failure, interruption ---------------------------------------------


def test_documents_are_not_retained_and_sample_ceiling_is_hard(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs: list[weakref.ref[CanonicalDocument]] = []
    original = fit_module.iter_plan_documents

    def tracked(gate: Any, **kwargs: Any) -> Iterator[Any]:
        for item, doc in original(gate, **kwargs):
            assert sum(r() is not None for r in refs) <= 3
            refs.append(weakref.ref(doc))
            yield item, doc

    monkeypatch.setattr(fit_module, "iter_plan_documents", tracked)
    fit(c05, write_policy(tmp_path / "a.yaml", c05), tmp_path / "a")
    assert len(refs) == 2 * c05["completion"]["documents"]  # Exactly two sequential passes.
    monkeypatch.setattr(fit_module, "MAX_SAMPLE_DOCUMENTS", 3)
    with pytest.raises(C05Error, match="retained sample document ceiling"):
        fit(c05, write_policy(tmp_path / "b.yaml", c05), tmp_path / "b")
    assert not (tmp_path / "b/fit").exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError])
def test_interrupted_or_failed_fit_publishes_nothing(
    c05: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: type[BaseException],
) -> None:
    observed: dict[str, Any] = {}

    def interrupted(cls: Any, documents: Any, **kwargs: Any) -> Any:
        for number, _ in enumerate(documents):
            if number == 3:
                break
        observed["spool_dir"] = kwargs["spool_dir"]
        observed["existed"] = kwargs["spool_dir"].is_dir()
        raise failure("authored interruption inside the BPE fit")

    monkeypatch.setattr(ByteLevelBPETokenizer, "train_from_documents", classmethod(interrupted))
    args = cli_args(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert main([*args, "--plan-only", "--no-progress"]) == 0
    digest = json.loads(capsys.readouterr().out)["resource_plan_digest"]
    code = main([*args, *signing(digest), "--no-progress"])
    assert code == (130 if failure is KeyboardInterrupt else 1)
    assert observed["existed"] and not observed["spool_dir"].exists()
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))
    assert not list((tmp_path / "scratch").iterdir())


def test_verification_failure_publishes_nothing(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise C05Error("authored verification failure")

    monkeypatch.setattr(fit_module, "_verify_tokenizer", refuse)
    with pytest.raises(C05Error, match="authored verification failure"):
        fit(c05, write_policy(tmp_path / "p.yaml", c05), tmp_path)
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))
    assert not list((tmp_path / "scratch").iterdir())


# -- tokenizer acceptance downstream ---------------------------------------------------


def test_changed_tokenizer_files_refuse(
    c05: dict[str, Any], fitted: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.exclusion.selection import count_tokens

    policy = load_fit_policy(fitted["policy"])[0]
    changed = tmp_path / "changed"
    shutil.copytree(fitted["fit"], changed)
    tokenizer_json = changed / TOKENIZER_DIR / "tokenizer.json"
    tokenizer_json.write_bytes(tokenizer_json.read_bytes() + b"\n")
    rebound = tmp_path / "rebound"
    shutil.copytree(fitted["fit"], rebound)
    binding_path = rebound / TOKENIZER_DIR / "c05-binding.json"
    binding = load(binding_path.read_bytes())
    binding["completion_digest"] = "0" * 64
    binding_path.write_bytes(canonical.canonical_bytes(binding))
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        with pytest.raises(C05Error, match="tokenizer files differ"):
            verify_fit(gate, changed, policy, c05["quotas"], c05["ifm"])
        with pytest.raises(C05Error, match="different C05 membership"):
            count_tokens(
                gate,
                rebound / TOKENIZER_DIR,
                tmp_path / "counts",
                ISSUER,
                KEY.encode(),
                scratch=tmp_path / "s",
            )
        # The production branch never weakens baseline certification.
        with pytest.raises(C05Error, match="baseline certification"):
            fit_module._verify_tokenizer(
                fitted["fit"] / TOKENIZER_DIR,
                gate,
                policy,
                True,
                fitted["body"]["sample"]["training_input_hash"],
            )


def cli_args(c05: dict[str, Any], policy: Path, out: Path) -> list[str]:
    return [
        "fit-tokenizer",
        "--c05-proof",
        str(c05["proof"]),
        "--fit-shares",
        str(policy),
        "--quotas",
        str(c05["quotas"]),
        "--ifm-split",
        str(c05["ifm"]),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "fit"),
        "--deficit-report",
        str(out / "deficit.json"),
    ]


def signing(digest: str) -> list[str]:
    return ["--resource-plan-digest", digest, "--issuer", ISSUER, "--key-env", KEY_ENV]


def test_operator_cli_plan_fit_verify_count_and_select(
    c05: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from xlm.data.exclusion.operator import main as operator

    policy = write_policy(tmp_path / "p.yaml", c05)
    args = cli_args(c05, policy, tmp_path)
    assert operator([*args, "--plan-only", "--no-progress"]) == 0
    planned = json.loads(capsys.readouterr().out)
    digest = planned["resource_plan_digest"]
    plan = planned["resource_plan"]
    assert plan["sample"]["target_bytes"] == TARGET
    assert plan["inputs"]["sequential_passes"] == 2 and plan["network_required"] is False
    assert not (tmp_path / "fit").exists()
    # The fit refuses without the reviewed plan digest, or with a different one.
    assert operator([*args, "--issuer", ISSUER, "--key-env", KEY_ENV, "--no-progress"]) == 1
    assert "resource-plan-digest" in capsys.readouterr().out
    assert operator([*args, *signing("0" * 64), "--no-progress"]) == 1
    capsys.readouterr()
    assert operator([*args, *signing(digest)]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["mode"] == "authored" and result["production"] is False
    assert "[C06] PROOF VERIFY | started" in captured.err
    assert not re.search(r"[0-9a-f]{16}", captured.err)
    # Write-once: a second fit to the same output refuses before any work.
    assert operator([*args, *signing(digest), "--no-progress"]) == 1
    capsys.readouterr()
    verify = [
        "verify-tokenizer-fit",
        "--c05-proof",
        str(c05["proof"]),
        "--fit-shares",
        str(policy),
        "--quotas",
        str(c05["quotas"]),
        "--ifm-split",
        str(c05["ifm"]),
        "--fit",
        str(tmp_path / "fit"),
    ]
    assert operator(verify) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    tokenizer = str(tmp_path / "fit" / TOKENIZER_DIR)
    common = ["--c05-proof", str(c05["proof"]), "--tokenizer", tokenizer]
    signer = ["--issuer", ISSUER, "--key-env", KEY_ENV]
    counting = ["--scratch", str(tmp_path / "s"), "--output", str(tmp_path / "counts")]
    assert operator(["count-tokens", *common, *counting, *signer]) == 0
    capsys.readouterr()
    code = operator(
        [
            "select",
            *common,
            "--scratch",
            str(tmp_path / "s"),
            "--counts",
            str(tmp_path / "counts"),
            "--quotas",
            str(c05["quotas"]),
            "--ifm-split",
            str(c05["ifm"]),
            "--deficit-report",
            str(tmp_path / "select-deficit.json"),
            "--output",
            str(tmp_path / "selection"),
            *signer,
        ]
    )
    assert code == 0, capsys.readouterr().out
    selection = load((tmp_path / "selection/selection.json").read_bytes())["payload"]
    assert selection["tokenizer"]["fingerprint"] == result["tokenizer_fingerprint"]


# -- detached protected volume ---------------------------------------------------------


def test_mounted_protected_volume_refuses_before_any_data_access(
    flow: dict[str, Any],  # noqa: F811 (detached-volume fixture)
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proof = proof_for(flow, tmp_path, monkeypatch)
    touched: list[str] = []
    for name in ("plan_from_proof", "iter_plan_documents", "load_fit_policy", "fit_tokenizer"):
        monkeypatch.setattr(fit_module, name, lambda *a, _n=name, **k: touched.append(_n))
    out = tmp_path / "work"
    args = [
        "fit-tokenizer",
        "--c05-proof",
        str(proof),
        "--fit-shares",
        str(PRODUCTION_POLICY),
        "--ifm-split",
        str(tmp_path / "ifm.json"),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "fit"),
        "--deficit-report",
        str(out / "deficit.json"),
    ]
    for extra in (["--plan-only"], signing("0" * 64)):
        assert main([*args, *extra, "--no-progress"]) == 1
        assert "volume is mounted" in capsys.readouterr().out
    assert touched == [] and not out.exists()
    # Detached: a fit/scratch/output path inside the protected root or C05 scratch refuses.
    layout = flow["layout"]
    shutil.move(layout.root, tmp_path / "detached-root")
    for blocked, name in (
        (layout.root / "fit", "protected benchmark root"),
        (layout.scratch / "fit", "C05 protected scratch"),
    ):
        moved = [*args]
        moved[moved.index("--output") + 1] = str(blocked)
        assert main([*moved, "--plan-only", "--no-progress"]) == 1
        assert name in capsys.readouterr().out
    assert touched == []
