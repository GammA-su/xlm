"""C06 fast fit over a c05-production-v3 completion (``c05_membership_v3``).

Two authored C05 runs over the same generated corpus, through the actual operator flow
(``c05_policy_support.build_run``), both with diagnostic_val and audit partitions:

* old chain: c05-production-v2 policy, ``c05_membership_v2`` rows (``lineage_group``);
* new chain: c05-production-v3 policy, ``c05_membership_v3`` rows (``split_group`` and
  ``exclusion_group``).

The production C06 fast path (``fit-tokenizer``, kept index, ``verify-tokenizer-fit``,
``verify-kept-index``) runs on both. The fit sample is checked against an independent
oracle that reads only ``doc_id``, ``content``, ``bytes``, ``split`` and the plan file
allocation of each membership row, so no group field can influence it. Authored
fixtures only; no real corpus, proof or tokenizer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from c05_policy_support import build_run
from test_c06_fast import fast_fit
from test_c06_tokenizer_fit import PRODUCTION_POLICY, write_policy
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitscan
from xlm.data.exclusion.fitfast import (
    FIT_PATH,
    KEPT_INDEX_DIR,
    membership_tables,
    open_streamed,
    verify_fit_fast,
    verify_kept_index,
)
from xlm.data.exclusion.keptindex import open_index
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import (
    C05Error,
    MatcherPolicyV4,
    ProductionPolicy,
    ProductionPolicyV3,
    TriggerPolicy,
)
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selection import allocation_key
from xlm.data.exclusion.tokenizer_fit import (
    FIT_MANIFEST,
    FIT_SAMPLE,
    TOKENIZER_DIR,
    apportion,
    load_fit_policy,
)

load = canonical.loads_bytes_strict
REPO = Path(__file__).resolve().parents[1]
# The frozen Mix-01 fit policy, reused unchanged for the policy-v2 C06 fit.
FIT_POLICY_DIGEST = "9db3872b486c20a6659b996b6d7f9ffad335d64fb69c4410b5fa72f83637667c"
REVIEWED_UNCOVERED = 2  # c05_policy_support's benchmark (see test_c05_policy_v2_flow)
PARTITIONS = {"diagnostic_bytes": 4096, "quick_bytes": 0, "audit_bytes": 4096}
GROUP_FIELDS = ("lineage_group", "split_group", "exclusion_group")


def old_policy() -> ProductionPolicy:
    return ProductionPolicy(matcher=MatcherPolicyV4(), **PARTITIONS)


def new_policy() -> ProductionPolicyV3:
    return ProductionPolicyV3(
        trigger=TriggerPolicy(reviewed_items_without_active_trigger=REVIEWED_UNCOVERED),
        **PARTITIONS,
    )


def chain(root: Path, policy: Any) -> dict[str, Any]:
    built = build_run(root, matcher=MatcherPolicyV4(), policy=policy, require_deficit=False)
    proof = load(built["proof"].read_bytes())
    directory = Path(proof["completion"])
    decisions = [
        load(line)
        for line in next((root / "scratch").rglob("decisions.jsonl")).read_bytes().splitlines()
    ]
    return {
        **built,
        "ifm": root / "ifm-split.json",
        "plan_digest": proof["plan_digest"],
        "completion_digest": proof["completion_digest"],
        "membership_bytes": (directory / "membership.jsonl").read_bytes(),
        "decisions": decisions,
    }


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


@pytest.fixture(scope="module")
def old(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    yield chain(tmp_path_factory.mktemp("old-chain") / "root", old_policy())


@pytest.fixture(scope="module")
def new(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    yield chain(tmp_path_factory.mktemp("new-chain") / "root", new_policy())


def fitted(run: dict[str, Any], out: Path, **options: Any) -> dict[str, Any]:
    policy = write_policy(out / "policy.yaml", run)
    envelope = fast_fit(run, policy, out, **options)
    return {"out": out, "policy": policy, "fit": out / "fit", "body": envelope["payload"]}


@pytest.fixture(scope="module")
def old_fit(old: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setenv(KEY_ENV, KEY)
        return fitted(old, tmp_path_factory.mktemp("old-fit"))


@pytest.fixture(scope="module")
def new_fit(new: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setenv(KEY_ENV, KEY)
        return fitted(new, tmp_path_factory.mktemp("new-fit"))


def rows_of(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [load(line) for line in run["membership_bytes"].splitlines()]


def view_of(run: dict[str, Any]) -> Any:
    return open_streamed(run["proof"], allow_authored=True, consumes=[])


def tables_for(run: dict[str, Any], fit: dict[str, Any], contract: str | None = None) -> Any:
    policy = load_fit_policy(fit["policy"])[0]
    tables = membership_tables(view_of(run), policy, sorted(fit["body"]["allocations"]))
    return tables if contract is None else replace(tables, contract=contract)


def parse(tables: Any, data: bytes) -> fitscan.MembershipChunk:
    fitscan.init_worker(tables)
    try:
        return fitscan.parse_membership_chunk(data)
    finally:
        fitscan._TABLES = None


def file_allocations(run: dict[str, Any]) -> dict[str, str]:
    plan = load(run["plan"].read_bytes())
    plan = plan.get("payload", plan)
    return {
        item["path"]: allocation_key(item["component"], item["view"], item["upstream_component"])
        for item in plan["files"]
    }


def oracle_sample(run: dict[str, Any], fit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Independent C06 selection from the kept rows' identity fields only.

    Eligible: C05 kept, assigned split ``train``, at most the fit cap. Order: sha256 of
    canonical [tag, seed, allocation, doc_id, content], then doc_id. Each allocation takes
    the shortest rank-ordered prefix reaching its budget (crossing document whole).
    """
    policy = load_fit_policy(fit["policy"])[0]
    owner = file_allocations(run)
    candidates: dict[str, list[tuple[bytes, str, str, int]]] = defaultdict(list)
    for row in rows_of(run):
        if row["decision"] != "kept" or row["split"] != "train":
            continue
        if row["bytes"] > policy.max_document_bytes:
            continue
        allocation = owner[row["file"]]
        rank = hashlib.sha256(
            canonical.canonical_bytes(
                ["c06-tokenizer-fit-v1", policy.seed, allocation, row["doc_id"], row["content"]]
            )
        ).digest()
        candidates[allocation].append((rank, row["doc_id"], row["content"], row["bytes"]))
    chosen: dict[str, dict[str, Any]] = {}
    for allocation, signed_row in fit["body"]["allocations"].items():
        held = 0
        for rank, doc_id, content, size in sorted(candidates[allocation]):
            if held >= signed_row["requested_bytes"]:
                break
            held += size
            chosen[doc_id] = {
                "doc_id": doc_id,
                "content": content,
                "allocation": canonical.loads_strict(allocation),
                "bytes": size,
                "rank": rank.hex(),
            }
    return chosen


def sample_of(fit: dict[str, Any]) -> list[dict[str, Any]]:
    return [load(line) for line in (fit["fit"] / FIT_SAMPLE).read_bytes().splitlines()]


def tree_digest(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): file_sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# -- the frozen fit policy is reused unchanged -------------------------------------------------


def test_production_fit_policy_is_reused_unchanged() -> None:
    policy, _ = load_fit_policy(PRODUCTION_POLICY)
    assert policy.identity() == FIT_POLICY_DIGEST
    assert policy.mode == "production" and policy.policy_id == "mix01_fit_shares_v1"
    assert policy.tokenizer.type == "byte_level_bpe"
    assert policy.tokenizer.target_vocab_size == 32768
    assert policy.tokenizer.special_tokens == ["<pad>", "<bos>", "<eos>", "<unk>"]
    assert policy.seed == 20260919
    assert policy.target_sample_bytes == 512 * 1024**2
    assert policy.rules.eligibility == "c05_kept_train_exact_content"
    assert len(policy.component_weights) == 11
    assert set(policy.component_weights.values()) == {1}
    # Deterministic, exact component shares of the 512 MiB budget.
    shares = apportion(policy.target_sample_bytes, policy.component_weights)
    assert shares == apportion(policy.target_sample_bytes, dict(policy.component_weights))
    assert sum(shares.values()) == policy.target_sample_bytes
    assert max(shares.values()) - min(shares.values()) == 1


# -- membership contracts -----------------------------------------------------------------------


def test_chains_carry_their_membership_contracts(old: dict[str, Any], new: dict[str, Any]) -> None:
    assert view_of(new).plan.output_contract == "c05_membership_v3"
    assert view_of(old).plan.output_contract == "c05_membership_v2"
    assert all(row.keys() == fitscan.MEMBERSHIP_KEYS_V3 for row in rows_of(new))
    assert all(row.keys() == fitscan.MEMBERSHIP_KEYS for row in rows_of(old))
    completion = load((Path(load(new["proof"].read_bytes())["completion"]) / "completion.json")
                      .read_bytes())["payload"]  # fmt: skip
    assert completion["kind"] == "c05_completion_v3"
    assert completion["output_contract"] == "c05_membership_v3"
    # The fixture is meaningful: every non-train or non-kept class is present.
    decided = {row["decision"] for row in new["decisions"]}
    assert {"kept", "excluded", "duplicate"} <= decided
    assert {"train", "diagnostic_val", "audit"} <= {row["split"] for row in rows_of(new)}


def test_membership_parser_accepts_only_the_plans_contract(
    old: dict[str, Any], new: dict[str, Any], old_fit: dict[str, Any], new_fit: dict[str, Any]
) -> None:
    v3 = parse(tables_for(new, new_fit), new["membership_bytes"])
    assert v3.rows == len(rows_of(new))
    v2 = parse(tables_for(old, old_fit), old["membership_bytes"])  # legacy stays readable
    assert v2.rows == len(rows_of(old))
    with pytest.raises(C05Error, match="membership record schema"):
        parse(tables_for(new, new_fit, "c05_membership_v2"), new["membership_bytes"])
    with pytest.raises(C05Error, match="membership record schema"):
        parse(tables_for(old, old_fit, "c05_membership_v3"), old["membership_bytes"])
    with pytest.raises(C05Error, match="unknown C05 membership contract"):
        parse(tables_for(new, new_fit, "c05_membership_v4"), new["membership_bytes"])


def test_split_group_and_exclusion_group_never_drive_c06(
    new: dict[str, Any], new_fit: dict[str, Any]
) -> None:
    """Swapping the two group values (or one for the other) changes no parsed column."""
    tables = tables_for(new, new_fit)
    reference = parse(tables, new["membership_bytes"])
    rows = rows_of(new)
    assert any(r["split_group"] != r["exclusion_group"] for r in rows)
    for transform in (
        lambda r: {**r, "split_group": r["exclusion_group"], "exclusion_group": r["split_group"]},
        lambda r: {**r, "exclusion_group": r["split_group"]},
        lambda r: {**r, "split_group": r["exclusion_group"]},
    ):
        data = b"".join(canonical.canonical_bytes(transform(r)) + b"\n" for r in rows)
        changed = parse(tables, data)
        for name in ("ids", "id_lengths", "file", "row", "nbytes", "content", "split",
                     "allocation", "rank"):  # fmt: skip
            assert np.array_equal(
                np.asarray(getattr(changed, name)), np.asarray(getattr(reference, name))
            ), name
    # Only the parser's schema table names the group fields; no C06/downstream module
    # reads them (so one can never be taken for the other).
    package = REPO / "src/xlm/data/exclusion"
    for module in ("fitfast", "keptindex", "tokenizer_fit", "gates", "selection",
                   "countfast", "selectfast", "freeze", "transport"):  # fmt: skip
        text = (package / f"{module}.py").read_text(encoding="utf-8")
        for name in GROUP_FIELDS:
            assert name not in text, (module, name)
    schema = (package / "fitscan.py").read_text(encoding="utf-8")
    uses = re.findall(r"split_group|exclusion_group", schema)
    assert len(uses) == 4  # MEMBERSHIP_KEYS_V3 and the schema's group-field tuple
    assert schema.count('"split_group", "exclusion_group"') == 2


# -- the C06 fast fit on the new chain -------------------------------------------------------------


def test_new_fit_is_bound_to_the_new_chain(new: dict[str, Any], new_fit: dict[str, Any]) -> None:
    body = new_fit["body"]
    assert body["fit_path"] == FIT_PATH
    assert (body["plan_digest"], body["completion_digest"]) == (
        new["plan_digest"],
        new["completion_digest"],
    )
    assert body["policy_digest"] == load_fit_policy(new_fit["policy"])[0].identity()
    binding = load((new_fit["fit"] / TOKENIZER_DIR / "c05-binding.json").read_bytes())
    assert binding == {
        "plan_digest": new["plan_digest"],
        "completion_digest": new["completion_digest"],
        "tokenizer_fingerprint": body["tokenizer"]["fingerprint"],
    }
    index = load((new_fit["fit"] / KEPT_INDEX_DIR / "kept-index.json").read_bytes())
    assert index["digest"] == body["kept_index"]["manifest_digest"]
    payload = index["payload"]
    assert (payload["plan_digest"], payload["completion_digest"]) == (
        new["plan_digest"],
        new["completion_digest"],
    )
    resource = load((new_fit["fit"] / "tokenizer_fit_resource_plan.json").read_bytes())["plan"]
    assert resource["completion_digest"] == new["completion_digest"]


def test_new_fit_sample_is_exactly_kept_train(new: dict[str, Any], new_fit: dict[str, Any]) -> None:
    sample = sample_of(new_fit)
    expected = oracle_sample(new, new_fit)
    assert [row["doc_id"] for row in sample] == sorted(expected)
    assert {row["doc_id"]: row for row in sample} == expected
    by_id = {row["doc_id"]: row for row in rows_of(new)}
    non_kept = {r["doc_id"] for r in new["decisions"] if r["decision"] != "kept"}
    held_out = {r["doc_id"] for r in rows_of(new) if r["split"] != "train"}
    assert non_kept and held_out
    for row in sample:
        assert by_id[row["doc_id"]]["split"] == "train"
        assert row["doc_id"] not in non_kept and row["doc_id"] not in held_out
    # Every allocation reached its budget with whole documents (crossing rule).
    for allocation, signed in new_fit["body"]["allocations"].items():
        got = sum(r["bytes"] for r in sample if canonical.canonical_bytes(r["allocation"])
                  .decode() == allocation)  # fmt: skip
        assert got == signed["selected_bytes"] >= signed["requested_bytes"]


def test_new_kept_index_is_c05_kept_membership(
    new: dict[str, Any], new_fit: dict[str, Any]
) -> None:
    view = view_of(new)
    policy = load_fit_policy(new_fit["policy"])[0]
    result = verify_kept_index(
        view,
        new_fit["fit"] / KEPT_INDEX_DIR,
        policy,
        new["quotas"],
        new["ifm"],
        membership=True,
        sources=True,
        workers=1,
        inline=True,
    )
    rows = rows_of(new)
    splits = {name: sum(r["split"] == name for r in rows) for name in fitscan.SPLIT_NAMES}
    assert result["rows"] == len(rows) == view.completion["kept"]
    assert result["assigned_splits"] == splits and splits["train"] > 0
    assert (
        result["train_canonical_bytes"]
        == sum(r["bytes"] for r in rows if r["split"] == "train")
        == sum(a["train_bytes"] for a in view.completion["allocations"].values())
    )
    assert (result["plan_digest"], result["completion_digest"]) == (
        new["plan_digest"],
        new["completion_digest"],
    )
    index = open_index(new_fit["fit"] / KEPT_INDEX_DIR, view)
    ids = {index.doc_id(n) for n in range(len(index))}
    assert ids == {r["doc_id"] for r in rows}
    assert not ids & {r["doc_id"] for r in new["decisions"] if r["decision"] != "kept"}


def test_new_fit_verifies_and_is_deterministic(
    new: dict[str, Any], new_fit: dict[str, Any], tmp_path: Path
) -> None:
    view = view_of(new)
    policy = load_fit_policy(new_fit["policy"])[0]
    result = verify_fit_fast(
        view, new_fit["fit"], policy, new["quotas"], new["ifm"], workers=1, sources=True,
        inline=True,
    )  # fmt: skip
    assert result["verified"] and result["sources_rehashed"]
    assert result["tokenizer_fingerprint"] == new_fit["body"]["tokenizer"]["fingerprint"]
    # A second fit (spawned workers) of the same chain: identical bytes everywhere.
    again = fitted(new, tmp_path, workers=2)
    for name in ("sample", "allocations", "components", "totals", "bpe_spool", "tokenizer",
                 "plan_digest", "completion_digest", "policy_digest"):  # fmt: skip
        assert again["body"][name] == new_fit["body"][name], name
    for part in (FIT_SAMPLE, TOKENIZER_DIR, KEPT_INDEX_DIR):
        first, second = new_fit["fit"] / part, again["fit"] / part
        if first.is_dir():
            assert tree_digest(first) == tree_digest(second), part
        else:
            assert file_sha(first) == file_sha(second), part


# -- legacy chain readable; stale and cross-chain artifacts refused --------------------------------


def test_legacy_v2_chain_still_fits_and_verifies(
    old: dict[str, Any], old_fit: dict[str, Any]
) -> None:
    view = view_of(old)
    policy = load_fit_policy(old_fit["policy"])[0]
    assert old_fit["body"]["completion_digest"] == old["completion_digest"]
    assert verify_fit_fast(
        view, old_fit["fit"], policy, old["quotas"], old["ifm"], workers=1, inline=True
    )["verified"]
    assert sample_of(old_fit) == list(dict(sorted(oracle_sample(old, old_fit).items())).values())


def test_cross_chain_fits_and_indexes_refuse(
    old: dict[str, Any], new: dict[str, Any], old_fit: dict[str, Any], new_fit: dict[str, Any]
) -> None:
    policy = load_fit_policy(new_fit["policy"])[0]
    for run, fit in ((old, new_fit), (new, old_fit)):
        view = view_of(run)
        # The old proof against the new fit, and the new proof against the stale fit.
        with pytest.raises(C05Error):
            verify_fit_fast(view, fit["fit"], policy, run["quotas"], run["ifm"], workers=1,
                            inline=True)  # fmt: skip
        with pytest.raises(C05Error, match="stale against C05"):
            open_index(fit["fit"] / KEPT_INDEX_DIR, view)
        with pytest.raises(C05Error, match="stale against C05"):
            verify_kept_index(view, fit["fit"] / KEPT_INDEX_DIR, policy, run["quotas"],
                              run["ifm"], workers=1, inline=True)  # fmt: skip


def cli_common(run: dict[str, Any], fit: dict[str, Any]) -> list[str]:
    return [
        "--c05-proof",
        str(run["proof"]),
        "--fit-shares",
        str(fit["policy"]),
        "--quotas",
        str(run["quotas"]),
        "--ifm-split",
        str(run["ifm"]),
    ]


def pins(run: dict[str, Any]) -> list[str]:
    return [
        "--expect-c05-plan-digest",
        run["plan_digest"],
        "--expect-c05-completion-digest",
        run["completion_digest"],
    ]


def test_cli_pins_refuse_the_old_chain_before_any_read(
    old: dict[str, Any],
    new: dict[str, Any],
    new_fit: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fit_args(run: dict[str, Any], output: Path) -> list[str]:
        return [
            "fit-tokenizer",
            *cli_common(run, new_fit),
            "--scratch",
            str(tmp_path / "scratch"),
            "--output",
            str(output),
            "--deficit-report",
            str(tmp_path / "deficit.json"),
            "--workers",
            "1",
            "--free-reserve-gib",
            "0",
            "--no-progress",
        ]

    # The new chain with its own pins plans normally.
    assert operator([*fit_args(new, tmp_path / "fit"), *pins(new), "--plan-only"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["resource_plan"]["completion_digest"] == new["completion_digest"]
    # The old proof with the new chain's pins refuses before any read or write.
    scratch = tmp_path / "scratch"
    if scratch.exists():
        scratch.rmdir()
    only_plan = ["--expect-c05-plan-digest", new["plan_digest"]]
    only_completion = ["--expect-c05-completion-digest", new["completion_digest"]]
    for only in (only_plan, only_completion, pins(new)):
        assert operator([*fit_args(old, tmp_path / "fit"), *only, "--plan-only"]) == 1
        assert not scratch.exists() and not (tmp_path / "fit").exists()
    signing = ["--issuer", ISSUER, "--key-env", KEY_ENV, "--resource-plan-digest", "0" * 64]
    assert operator([*fit_args(old, tmp_path / "fit"), *pins(new), *signing]) == 1
    assert not scratch.exists() and not (tmp_path / "fit").exists()
    # Verification commands honour the same pins.
    index = ["--index", str(new_fit["fit"] / KEPT_INDEX_DIR), "--no-progress", "--workers", "1"]
    capsys.readouterr()
    assert operator(["verify-kept-index", *cli_common(new, new_fit), *index, *pins(new)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["completion_digest"] == new["completion_digest"]
    assert report["assigned_splits"]["train"] == sum(r["split"] == "train" for r in rows_of(new))
    assert operator(["verify-kept-index", *cli_common(old, new_fit), *index, *pins(new)]) == 1
    verify = ["--fit", str(new_fit["fit"]), "--no-progress", "--workers", "1"]
    assert operator(["verify-tokenizer-fit", *cli_common(new, new_fit), *verify, *pins(new)]) == 0
    assert operator(["verify-tokenizer-fit", *cli_common(new, new_fit), *verify, *pins(old)]) == 1


def test_published_fits_are_write_once_and_stale_tokenizers_refused(
    new: dict[str, Any],
    old_fit: dict[str, Any],
    new_fit: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Re-fitting into an existing (historical) output refuses and changes nothing.
    before = tree_digest(old_fit["fit"])
    fit_args = [
        "fit-tokenizer",
        *cli_common(new, new_fit),
        "--scratch",
        str(tmp_path / "scratch"),
        "--output",
        str(old_fit["fit"]),
        "--deficit-report",
        str(tmp_path / "deficit.json"),
        "--workers",
        "1",
        "--free-reserve-gib",
        "0",
        "--no-progress",
    ]
    assert operator([*fit_args, "--plan-only"]) == 0
    digest = json.loads(capsys.readouterr().out)["resource_plan_digest"]
    signing = ["--issuer", ISSUER, "--key-env", KEY_ENV, "--resource-plan-digest", digest]
    assert operator([*fit_args, *signing]) == 1
    assert tree_digest(old_fit["fit"]) == before
    assert not (tmp_path / "deficit.json").exists()
    # The old chain's C06 tokenizer cannot count the new chain.
    chain_args = [
        "count-tokens",
        "--c05-proof",
        str(new["proof"]),
        "--tokenizer",
        str(old_fit["fit"] / TOKENIZER_DIR),
        "--scratch",
        str(tmp_path / "count-scratch"),
        "--output",
        str(tmp_path / "counts"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
        "--workers",
        "1",
    ]
    assert operator(chain_args) == 1
    assert not (tmp_path / "counts").exists()
    # The new chain's C06 tokenizer counts it (the next production stage).
    chain_args[4] = str(new_fit["fit"] / TOKENIZER_DIR)
    assert operator(chain_args) == 0
    counts = load((tmp_path / "counts/counts.json").read_bytes())["payload"]
    assert counts["completion_digest"] == new["completion_digest"]
    tokenizer = load((new_fit["fit"] / TOKENIZER_DIR / "c05-binding.json").read_bytes())
    assert counts["tokenizer"]["fingerprint"] == tokenizer["tokenizer_fingerprint"]
    assert counts["tokenizer"]["c05_fit_binding"] is True
    assert (
        load((new_fit["fit"] / FIT_MANIFEST).read_bytes())["payload"]["tokenizer"]["fingerprint"]
        == tokenizer["tokenizer_fingerprint"]
    )
