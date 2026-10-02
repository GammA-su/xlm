"""IFM row contract v2, per-adapter code identity and admission repair (offline, authored).

The retained General shard of p02 rank 0 failed on 2 exact-empty ``text`` rows
of 329,409 (rows 4983 and 193631, both ``token_count`` 0); the frozen v1
adapter treats them like a missing column. v2 records them as
``IfmEmptyTextError`` while every structural fault stays fatal and accepted
rows stay byte-identical. Only the IFM adapters' code identity changes, so
only their bridges and admissions must be renewed; the plan that failed is
continued by an admission repair, never edited or re-run. Fixtures are
authored: no corpus text.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mix01_source_fixtures import REVISION
from test_ifm_production_bounds import ADMISSION as IFM_ADMISSION
from test_ifm_production_bounds import arguments as ifm_arguments
from test_ifm_production_bounds import pre_fix, roots_of, tree
from test_mix01_source_cli import load_cli
from test_source_plan import build, models
from test_source_repair import repair
from test_source_run import (  # noqa: F401 - pytest fixtures
    Served,
    loopback_only,  # noqa: F401 - pytest fixture
    served,
)
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.adapters import columns, ifm_adapters, mix01_adapters, registry
from xlm.data.adapters.ifm_adapters import IfmEmptyTextError
from xlm.data.adapters.mix01_adapters import MissingFieldError, RecordRejectedError
from xlm.data.adapters.rejections import (
    build_rejection_record,
    rejection_code,
    serialize_document,
    serialize_rejection,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id
from xlm.data.sources import certified_evidence as ce

VIEWS = ("general", "planning")
FILE = "general/general_full.chunk0-test-00000.parquet"
RENEWED = {**IFM_ADMISSION, "bridge_receipt_digest": "c" * 64}


def v1(view: str) -> Any:
    return mix01_adapters.ADAPTERS_BY_ID[f"ifm_{view}"]()


def v2(view: str) -> Any:
    return registry.ADAPTERS_BY_ID[f"ifm_{view}"]()


def adapt(adapter: Any, record: dict[str, Any], row: int = 7) -> Any:
    return adapter.adapt(record, source_file=FILE, source_row=row, source_revision="a" * 40)


# ------------------------------------------------------------- row contract


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize(
    "record",
    [
        {"text": "Authored complete document.\n\nSecond paragraph.", "token_count": 9},
        {"text": "  leading and trailing whitespace kept verbatim \n", "token_count": 0},
        {"text": "no declared count"},
        {"text": "null declared count", "token_count": None},
    ],
)
def test_accepted_rows_are_byte_identical_to_v1(view: str, record: dict[str, Any]) -> None:
    document = adapt(v2(view), record)
    assert serialize_document(document) == serialize_document(adapt(v1(view), record))
    assert document.text == record["text"]  # verbatim, never stripped
    assert document.doc_id == canonical_source_doc_id(f"ifm_behaviors:{view}", FILE, 7)
    assert adapt(v2(view), record).to_dict() == document.to_dict()  # deterministic
    assert adapt(v2(view), record, row=8).doc_id != document.doc_id


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize(
    ("text", "kind"),
    [("", "empty"), (" ", "whitespace-only"), ("\n\t 　", "whitespace-only")],
)
def test_blank_string_text_is_a_recorded_rejection(view: str, text: str, kind: str) -> None:
    record = {"text": text, "token_count": 0}
    with pytest.raises(MissingFieldError, match="non-empty upstream field 'text'"):
        adapt(v1(view), record)  # v1: the whole file fails
    with pytest.raises(IfmEmptyTextError) as caught:
        adapt(v2(view), record)
    error = caught.value
    assert isinstance(error, RecordRejectedError) and not isinstance(error, MissingFieldError)
    assert rejection_code(error) == "IfmEmptyTextError"
    assert str(error) == (
        f"adapter 'ifm_{view}' records a row whose upstream 'text' is {kind} "
        f"({len(text)} characters, declared token_count 0); it has no training content."
    )


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"token_count": 3}, "absent and cannot be guessed"),
        ({"text": None, "token_count": 0}, "absent and cannot be guessed"),
        ({"text": 5, "token_count": 1}, "to be a string"),
        ({"text": ["a"], "token_count": 1}, "to be a string"),
        ({"text": b"bytes", "token_count": 1}, "to be a string"),
        ({"text": "valid", "token_count": -1}, "non-negative integer"),
        ({"text": "valid", "token_count": True}, "non-negative integer"),
        ({"text": "valid", "token_count": "3"}, "non-negative integer"),
        ({"text": "valid", "token_count": 1.5}, "non-negative integer"),
        # A malformed count is never hidden behind a recordable empty-text rejection.
        ({"text": "", "token_count": -1}, "non-negative integer"),
        ({"text": " ", "token_count": "0"}, "non-negative integer"),
    ],
)
def test_structural_faults_stay_fatal_exactly_as_in_v1(
    view: str, record: dict[str, Any], message: str
) -> None:
    with pytest.raises(MissingFieldError, match=message) as new:
        adapt(v2(view), record)
    with pytest.raises(MissingFieldError) as old:
        adapt(v1(view), record)
    assert type(new.value) is type(old.value) is MissingFieldError
    if record.get("text") not in ("", " "):
        assert str(new.value) == str(old.value)


def test_rejection_ledger_line_is_deterministic_and_text_free() -> None:
    secret = "   "  # whitespace-only, so only its length may be recorded

    def line() -> str:
        with pytest.raises(IfmEmptyTextError) as caught:
            adapt(v2("general"), {"text": secret, "token_count": 2})
        return serialize_rejection(
            build_rejection_record(
                input_line=4984,
                source_id="ifm_behaviors",
                source_revision="a" * 40,
                source_file=FILE,
                source_row=4983,
                adapter_id="ifm_general",
                error=caught.value,
                original_record_sha256="d" * 64,
            )
        )

    first = line()
    assert first == line()
    record = json.loads(first)
    assert record["rejection_code"] == "IfmEmptyTextError"
    assert record["rejection_category"] == "policy"
    assert (record["source_row"], record["input_line"]) == (4983, 4984)
    assert secret not in first and "text" not in record


# ------------------------------------------------------- registry and identity


def frozen_identity() -> dict[str, str]:
    """The pre-change identity every stored bridge binds (mix01_adapters + columns)."""
    return {
        module.__name__: hashlib.sha256(
            Path(str(module.__file__)).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for module in (mix01_adapters, columns)
    }


def test_registry_overrides_only_the_ifm_adapters() -> None:
    from xlm.data.adapters import common_pile_adapters

    assert set(registry.ADAPTERS_BY_ID) == set(mix01_adapters.ADAPTERS_BY_ID)
    for adapter_id, factory in registry.ADAPTERS_BY_ID.items():
        if adapter_id in ("ifm_general", "ifm_planning"):
            assert factory is ifm_adapters.ADAPTERS_BY_ID[adapter_id]
            assert issubclass(factory, mix01_adapters.ADAPTERS_BY_ID[adapter_id])
        elif adapter_id == "common_pile":
            assert factory is common_pile_adapters.CommonPileAdapter
            assert issubclass(factory, mix01_adapters.CommonPileAdapter)
        else:
            assert factory is mix01_adapters.ADAPTERS_BY_ID[adapter_id]
    # Production adaptation resolves through the registry, Essential-Web keeps the frozen one.
    from xlm.data.acquisition import source_local

    assert source_local.ADAPTERS_BY_ID is registry.ADAPTERS_BY_ID


def test_only_ifm_code_identity_changes() -> None:
    from xlm.data.adapters import common_pile_adapters

    frozen = frozen_identity()
    for adapter_id in registry.ADAPTERS_BY_ID:
        identity = ce.adapter_code_identity(adapter_id)
        if adapter_id in ("ifm_general", "ifm_planning"):
            assert registry.adapter_code_modules(adapter_id) == (
                mix01_adapters,
                columns,
                ifm_adapters,
            )
            assert identity != frozen
            assert {k: identity[k] for k in frozen} == frozen
            assert set(identity) - set(frozen) == {"xlm.data.adapters.ifm_adapters"}
        elif adapter_id == "common_pile":
            assert registry.adapter_code_modules(adapter_id) == (
                mix01_adapters,
                columns,
                common_pile_adapters,
            )
            assert {k: identity[k] for k in frozen} == frozen
            assert set(identity) - set(frozen) == {"xlm.data.adapters.common_pile_adapters"}
        else:
            assert registry.adapter_code_modules(adapter_id) == (mix01_adapters, columns)
            assert identity == frozen  # every non-IFM bridge still verifies
    with pytest.raises(KeyError):
        registry.adapter_code_modules("unregistered")


@pytest.mark.parametrize("view", VIEWS)
def test_a_bridge_certified_under_v1_code_is_refused_for_ifm_only(
    view: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def pin(source_id: str, view_id: str, adapter_id: str) -> ce.SourcePin:
        return ce.SourcePin(
            source_id=source_id,
            view_id=view_id,
            component_id="c",
            provider="huggingface",
            repository="r/r",
            revision="a" * 40,
            adapter_id=adapter_id,
        )

    def stored(source: ce.SourcePin) -> dict[str, Any]:
        return {"source": source.as_dict(), "adapter": {"code_sha256": frozen_identity()}}

    ifm = pin("ifm_behaviors", view, f"ifm_{view}")
    other = pin("finewiki", "en", "finewiki_en")
    receipts = {ifm.view_id: stored(ifm), other.view_id: stored(other)}
    monkeypatch.setattr(ce, "stored_bridge", lambda store, s, v: receipts[v])
    monkeypatch.setattr(ce, "load_probe_evidence", lambda s, v, store: None)
    with pytest.raises(ce.BridgeRefusal, match="adapter code changed"):
        ce.verify_current(None, ifm)  # type: ignore[arg-type]
    # The non-IFM receipt passes the code check and fails only on the absent record.
    with pytest.raises(ce.BridgeRefusal, match="stored evidence record differs"):
        ce.verify_current(None, other)  # type: ignore[arg-type]


def test_a_plan_under_a_stale_admission_is_refused_by_the_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = load_cli()
    monkeypatch.setattr(cli, "resolve_verified_production_admission", lambda plan, store: None)
    monkeypatch.setattr(cli, "current_admission", lambda spec, store: dict(RENEWED))
    with pytest.raises(cli.DriverError, match="admission changed since this plan was made"):
        cli.admission_check(None, None, dict(IFM_ADMISSION))(None)
    cli.admission_check(None, None, dict(RENEWED))(None)  # a renewed plan passes


# --------------------------------------------------------------- planner


def test_admission_repair_under_unchanged_limits_is_a_distinct_repair() -> None:
    first = build()
    with pytest.raises(planner.PlanError, match="limits and admission are unchanged"):
        repair(first)  # identical limits and admission: resume, never repair
    renewed = {"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "c" * 64}
    record = repair(first, admission=renewed)
    assert record["repair"]["changed_limits"] == {}
    assert record["repair"]["changed_admission"] == {
        "bridge_receipt_digest": {"from": "b" * 64, "to": "c" * 64}
    }
    assert record["inputs"]["admission"] == renewed
    assert record["selection"]["ranks"] == [0]
    assert record["selection"]["next_cursor"] == first["selection"]["next_cursor"]
    assert "supersedes" not in record and "start_rank" not in record["selection"]
    assert record["expected"]["transfer_bytes"] == 0  # its rank's bytes were retained
    planner.check_plan(record)
    assert repair(first, admission=renewed)["digest"] == record["digest"]


def test_a_limit_repair_carries_no_admission_key(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build()
    key = (first["source"]["source_id"], first["source"]["view_id"])
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, key, (16 * 1024 * 1024, "authored v2"))
    assert "changed_admission" not in repair(first)["repair"]  # earlier digests unchanged


def test_planning_p02_is_superseded_again_after_its_admission_is_renewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = roots_of(tmp_path, "planning")
    first = pre_fix("planning", monkeypatch)
    runner.store_plan(roots, first)
    cli = load_cli()
    second = planner.build_plan(**ifm_arguments("planning"), superseded=cli.superseded_of(roots, 1))
    runner.store_plan(roots, second)
    before = {1: tree(roots, 1), 2: tree(roots, 2)}
    with pytest.raises(planner.PlanError, match="reproduce the unauthorized plan"):
        planner.build_plan(**ifm_arguments("planning"), superseded=cli.superseded_of(roots, 2))
    third = planner.build_plan(
        **{**ifm_arguments("planning"), "admission": RENEWED},
        superseded=cli.superseded_of(roots, 2),
    )
    runner.store_plan(roots, third)
    assert third["supersedes"]["plan_digest"] == second["digest"]
    assert third["supersedes"]["changed_sections"] == ["inputs"]
    assert third["supersedes"]["changed_limits"] == {}
    assert third["selection"]["files"] == second["selection"]["files"]
    assert third["selection"]["next_cursor"] == 2
    assert {1: tree(roots, 1), 2: tree(roots, 2)} == before  # never edited
    for stale in (1, 2):
        with pytest.raises(runner.RunError, match=f"superseded by plan {stale + 1}"):
            runner.authorize(roots, stale, runner.load_plan(roots, stale)["digest"], "t", print)
    records = [runner.load_plan(roots, s) for s in roots.sequences()]
    assert runner.resolution(roots, records) == {1: [], 2: [], 3: [0, 1]}
    assert runner.sufficiency(roots)["supersessions"] == [
        {"sequence": 2, "supersedes_sequence": 1},
        {"sequence": 3, "supersedes_sequence": 2},
    ]


# ------------------------------------------------- end to end (loopback)

ROWS = 300
GROUP = 100
EMPTY_ROW = 157
PIN = {
    "source_id": "ifm_behaviors",
    "view_id": "general",
    "component_id": "ifm_behaviors_general_planning",
    "provider": "huggingface",
    "repository": "IFM/Pretrain-Behaviors",
    "revision": REVISION,
    "adapter_id": "ifm_general",
}


def ifm_row(file: int, row: int) -> dict[str, Any]:
    text = f"Authored IFM document {file}-{row}: plan, act, check.\n" * (2 + row % 4)
    return {"text": text, "token_count": 12 * (2 + row % 4)}


def ifm_file(file: int, *, empty: bool) -> list[dict[str, Any]]:
    rows = [ifm_row(file, r) for r in range(ROWS)]
    if empty:
        rows[EMPTY_ROW] = {"text": "", "token_count": 0}
    return rows


def parquet(rows: list[dict[str, Any]]) -> bytes:
    schema = pa.schema([("text", pa.string()), ("token_count", pa.int64())])
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), buffer, row_group_size=GROUP)
    return buffer.getvalue()


class IfmWorld:
    def __init__(self, tmp_path: Path, state: Served, base: str) -> None:
        self.state, self.base = state, base
        self.roots = runner.Roots(tmp_path / "data", tmp_path / "scratch", "ifm_general")
        names = [f"general/general_full.chunk0-test-{i:05d}.parquet" for i in range(5)]
        seed = 20260918
        entries: list[dict[str, Any]] = sorted(
            (
                {
                    "file": name,
                    "size_bytes": None,
                    "order_key": hashlib.sha256(
                        f"{seed}|{PIN['repository']}|{REVISION}|{name}".encode()
                    ).hexdigest(),
                }
                for name in names
            ),
            key=lambda e: (e["order_key"], e["file"]),
        )
        self.inventory: dict[str, Any] = {
            "inventory_version": 1,
            "source_id": PIN["source_id"],
            "repository": PIN["repository"],
            "revision": REVISION,
            "seed": seed,
            "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
            "files": entries,
            "file_count": len(names),
        }
        self.inventory["inventory_digest"] = planner.inventory_digest(self.inventory)
        self.ordered = [e["file"] for e in entries]
        # Rank 1 carries the empty row: inline processing seals rank 0 first.
        self.rows = {e["file"]: ifm_file(rank, empty=rank == 1) for rank, e in enumerate(entries)}
        for name, rows in self.rows.items():
            state.files[name] = parquet(rows)
        sizes = [len(payload) for payload in state.files.values()]
        texts = [len(r["text"].encode()) for rows in self.rows.values() for r in rows]
        self.layout = tp.SourceLayout(
            source_id=PIN["source_id"],
            file_bytes=max(sizes),
            group_rows=GROUP,
            group_bytes=max(sizes) // (ROWS // GROUP),
            projected_group_bytes=max(sizes) // (ROWS // GROUP) // 2,
            range_requests_per_group=2.0,
            metadata_requests_per_file=4,
            canonical_bytes_per_row=sum(texts) / len(texts),
            range_record_bytes_per_row=120.0,
            metadata_bytes_per_file=1_000,
            source_files=None,
            evidence={"rows": "0" * 64},
        )
        report = tp.evaluate(
            self.layout, tp.Requirement(PIN["source_id"], 10_000, 1.15), tp.Ceilings(), models()
        )
        assert report["selected_mode"] == "whole_file_local"
        self.policy = tp.freeze(report, basis="modeled", inputs={"model": "authored"})
        self.stream = io.StringIO()

    def common(self, admission: dict[str, str]) -> dict[str, Any]:
        # One sealed file already exceeds the requirement; the planner still selects two.
        per_file = self.layout.rows_per_file * self.layout.canonical_bytes_per_row
        tokens = int(0.95 * per_file / 4)
        component = PIN["component_id"]
        quotas = {"first_pass_headroom_quotas": {component: tokens}}
        estimate = {
            "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
            "sources": {
                component: {
                    "status": "ESTIMATED",
                    "first_pass_usable_token_target": tokens,
                    "required_canonical_bytes_base": tokens * 4.0,
                }
            },
        }
        return {
            "source_key": "ifm_general",
            "pin": PIN,
            "requirement": planner.requirement_from(
                component, quotas, estimate, quotas_sha256="1" * 64, estimate_sha256="2" * 64
            ),
            "inventory": self.inventory,
            "inventory_sha256": "3" * 64,
            "layout": self.layout,
            "calibration": {"rows": "0" * 64},
            "policy": self.policy,
            "admission": admission,
        }

    def run(self, sequence: int, **options: Any) -> dict[str, Any]:
        return runner.run_plan(
            self.roots,
            sequence,
            admitted=admitted,
            stream=self.stream,
            url_for=lambda name: f"{self.base}/resolve/{name}",
            download_workers=1,
            process_workers=0,
            **options,
        )

    def hits(self) -> dict[str, int]:
        return {name: self.state.hits(name) for name in self.state.files}


def admitted(plan: Any) -> None:
    assert plan.source_id == PIN["source_id"] and plan.revision == REVISION


def history(w: IfmWorld, sequence: int) -> dict[str, str]:
    found: dict[str, str] = {}
    for root in (w.roots.plan_dir(sequence), w.roots.canonical / f"p{sequence:02d}"):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                key = path.relative_to(w.roots.data_root).as_posix()
                found[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def expected_documents(rows: list[dict[str, Any]], name: str) -> str:
    """v1's own documents for every non-empty row, in file order."""
    digest = hashlib.sha256()
    for row, record in enumerate(rows):
        if record["text"].strip():
            document = v1("general").adapt(
                record, source_file=name, source_row=row, source_revision=REVISION
            )
            digest.update(serialize_document(document).encode("utf-8") + b"\n")
    return digest.hexdigest()


def test_admission_repair_resolves_only_the_failed_rank_offline_and_seals_once(
    tmp_path: Path,
    served: tuple[Served, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp, "READ_BYTES", 256)
    w = IfmWorld(tmp_path, *served)
    first = planner.build_plan(**w.common(IFM_ADMISSION))
    assert [e["file"] for e in first["selection"]["files"]] == w.ordered[:2]
    assert first["selection"]["next_cursor"] == 2
    runner.store_plan(w.roots, first)
    runner.authorize(w.roots, 1, first["digest"], "tester", admitted)

    # The p02-era run: the frozen v1 adapter fails the file holding the empty row.
    with monkeypatch.context() as patch:
        patch.setitem(registry.ADAPTERS_BY_ID, "ifm_general", mix01_adapters.IfmGeneralAdapter)
        with pytest.raises(MissingFieldError, match="non-empty upstream field 'text'"):
            w.run(1)
    failed = runner.read_json(w.roots.plan_dir(1) / "performance-00.json")
    assert failed["outcome"]["root_failure"]["exception"] == "MissingFieldError"
    assert failed["outcome"]["root_failure"]["key"] == "f00001"
    (sealed,) = runner.resume_state(w.roots, first)["receipts"]
    assert (sealed["rank"], sealed["rejected"]) == (0, 0)
    restart = runner.classify(w.roots, first, runner.resume_state(w.roots, first), "p01")
    assert restart["counts"]["sealed_skip"] == restart["counts"]["local_processing_retry"] == 1

    # Incomplete: the unresolved rank blocks the seal although the bytes suffice.
    status = runner.sufficiency(w.roots)
    assert status["acquired_canonical_bytes"] >= status["required_canonical_bytes"]
    assert status["status"] == "INCOMPLETE" and status["unresolved_ranks"] == {"1": [1]}
    assert status["next_cursor"] == 2
    with pytest.raises(runner.RunError, match="INCOMPLETE"):
        runner.first_pass_seal(w.roots)
    before = history(w, 1)

    # Same limits and admission: a resume, never a repair.
    repaired = load_cli().repaired_of(w.roots, 1)
    with pytest.raises(planner.PlanError, match="limits and admission are unchanged"):
        planner.build_repair_plan(**w.common(IFM_ADMISSION), repaired=repaired)
    record = planner.build_repair_plan(**w.common(RENEWED), repaired=repaired)
    runner.store_plan(w.roots, record)
    name = w.ordered[1]
    retained = sp.load_durable_source(w.roots.raw_path(name))
    assert retained is not None
    assert record["sequence"] == 2 and record["selection"]["ranks"] == [1]
    assert record["selection"]["next_cursor"] == 2
    assert record["repair"]["sealed_ranks"] == [0]
    assert record["repair"]["changed_limits"] == {}
    assert record["repair"]["changed_admission"] == {
        "bridge_receipt_digest": {"from": "b" * 64, "to": "c" * 64}
    }
    assert record["repair"]["retained_sha256"] == {name: retained["sha256"]}
    assert record["expected"]["transfer_bytes"] == record["expected"]["requests"] == 0
    resume = runner.resume_state(w.roots, record)
    restart = runner.classify(w.roots, record, resume, "p02")
    assert restart["counts"]["local_processing_retry"] == 1
    assert restart["counts"]["fresh_download"] == restart["known_network_bytes"] == 0

    # Neither plan runs without its own authorization; the repaired one never again.
    with pytest.raises(runner.RunError, match="repaired by a later plan"):
        w.run(1)
    with pytest.raises(runner.RunError, match="not authorized"):
        w.run(2, offline=True)
    runner.authorize(w.roots, 2, record["digest"], "tester", admitted)
    hits = w.hits()
    report = w.run(2, offline=True)
    assert report["outcome"]["status"] == "completed"
    assert w.hits() == hits  # zero network
    assert report["transfer"]["files"] == report["transfer"]["transferred_bytes"] == 0
    (unit,) = runner.resume_state(w.roots, record)["receipts"]
    assert (unit["rank"], unit["file"], unit["raw"]["sha256"]) == (1, name, retained["sha256"])
    assert (unit["rows"], unit["documents"], unit["rejected"]) == (ROWS, ROWS - 1, 1)
    assert unit["rejection_counts_by_code"] == {"IfmEmptyTextError": 1}
    assert unit["documents_sha256"] == expected_documents(w.rows[name], name)
    assert unit["transfer"]["transferred_bytes"] == 0

    # History is untouched; each rank is sealed once; the cursor never moves.
    assert history(w, 1) == before
    assert runner.verify_plan(w.roots, first, content=True)["units_verified"] == 1
    assert runner.verify_plan(w.roots, record, content=True)["units_verified"] == 1
    records = [runner.load_plan(w.roots, s) for s in w.roots.sequences()]
    assert runner.resolution(w.roots, records) == {1: [], 2: []}
    status = runner.sufficiency(w.roots)
    assert status["status"] == "SUFFICIENT"
    assert status["repairs"] == [{"sequence": 2, "repairs_sequence": 1, "ranks": [1]}]
    assert status["next_cursor"] == 2
    accounts = [runner.account(w.roots, r) for r in records]
    assert status["acquired_canonical_bytes"] == sum(a["canonical_bytes"] for a in accounts)
    seal = runner.first_pass_seal(w.roots)
    assert [(u["sequence"], u["rank"]) for u in seal["units"]] == [(1, 0), (2, 1)]
    assert sorted(u["file"] for u in seal["units"]) == sorted(w.ordered[:2])
    assert seal["plans"][0]["repaired_ranks"] == {"2": [1]}
    assert seal["plans"][1]["repair_of"] == {
        "plan_sequence": 1,
        "plan_digest": first["digest"],
        "ranks": [1],
        "changed_admission": record["repair"]["changed_admission"],
    }
    assert runner.first_pass_seal(w.roots)["digest"] == seal["digest"]  # written once
    assert (w.roots.plans / "first-pass-seal.json").is_file()
