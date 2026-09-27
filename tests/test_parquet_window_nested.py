"""Offline window-v2 (nested struct projection) sampling/acquisition tests.

Authored Wiki-Rewrite-shaped fixtures only: ``text``, ``license``,
``metadata: struct<category, models_used>``, ``uuid`` (plus unprojected
nested columns). Values are authored, never real Nemotron data; every byte
is served from local memory. Nothing touches the network or X:\\XLM.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionPlan,
    ParquetWindowDecode,
    PlanAuthorization,
    load_acquisition_plan,
)
from xlm.data.acquisition.projection import (
    LeafSpec,
    ProjectionRefusal,
    map_fields_to_leaves,
    resolve_projection,
)
from xlm.data.acquisition.records import RecordLimitError, encode_record
from xlm.data.acquisition.sampling import (
    FileLayout,
    SamplingRefusal,
    SamplingRequest,
    discover_layout_local,
    discover_layout_over_ranges,
    plan_sample_blocks,
)
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import (
    MissingFieldError,
    RecordRejectedError,
    WikiRewriteAdapter,
)
from xlm.data.sources.transport import BudgetExhaustedError

BIG_ROWS = 140_000
WIKI_PROJECTION = tuple(columns_for("wiki_rewrite"))
CATEGORY = "Nemotron-Pretraining-Wiki-Rewrite"
REVISION = "authored-rev-wiki"
FIXTURES = Path(__file__).parent / "fixtures" / "window_v1_synth"
V2 = ParquetWindowDecode(
    policy_version=2,
    stream_buffer_bytes=64 * 1024,
    max_window_scan_rows=16_384,
    batch_rows=256,
    ratio_exempt_bytes=1024 * 1024,
)
V1 = V2.model_copy(update={"policy_version": 1})
METADATA_TYPE = pa.struct([("category", pa.string()), ("models_used", pa.string())])
PROVENANCE_TYPE = pa.struct(
    [("source_hash", pa.string()), ("stats", pa.struct([("tokens", pa.int64())]))]
)
UNPROJECTED = {"provenance.source_hash", "provenance.stats.tokens", "tags.list.element"}


def _hex(index: int, salt: str) -> str:
    return hashlib.sha256(f"{salt}{index}".encode()).hexdigest()


def _wiki_table(rows: int, *, extra: bool = True, **columns: Any) -> pa.Table:
    """Authored Wiki-shaped rows; ``extra`` adds unprojected nested columns."""
    data: dict[str, Any] = {
        "text": [f"Authored rewrite {_hex(i, 't')}{_hex(i, 'u')}" for i in range(rows)],
        "license": ["CC-BY-4.0"] * rows,
        "metadata": pa.array(
            [
                {
                    "category": CATEGORY,
                    "models_used": None if i % 9 == 0 else f"model-{_hex(i, 'm')[:6]}",
                }
                for i in range(rows)
            ],
            type=METADATA_TYPE,
        ),
    }
    if extra:
        data["provenance"] = pa.array(
            [{"source_hash": _hex(i, "p"), "stats": {"tokens": i % 777}} for i in range(rows)],
            type=PROVENANCE_TYPE,
        )
    data["uuid"] = [f"uuid-{_hex(i, 'id')[:32]}" for i in range(rows)]
    if extra:
        data["tags"] = pa.array([[f"tag{i % 5}", _hex(i, "g")[:8]] for i in range(rows)])
    data.update(columns)
    return pa.table(data)


@pytest.fixture(scope="module")
def big_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wiki") / "part_000003.parquet"
    pq.write_table(
        _wiki_table(BIG_ROWS),
        path,
        row_group_size=BIG_ROWS,
        compression="zstd",
        data_page_size=64 * 1024,
    )
    return path


def _small_file(path: Path, groups: int = 3, rows: int = 1000, **kw: Any) -> Path:
    pq.write_table(_wiki_table(groups * rows, **kw), path, row_group_size=rows, compression="zstd")
    return path


def _layout(path: Path, name: str | None = None) -> dict[str, FileLayout]:
    label = name or path.name
    return {
        label: discover_layout_local(
            path, name=label, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
        )
    }


def _request(files: tuple[str, ...], **changes: Any) -> SamplingRequest:
    values: dict[str, Any] = {
        "source_id": "authored",
        "view_id": "Nemotron-Pretraining-Wiki-Rewrite",
        "revision": REVISION,
        "files": files,
        "seed": 20260918,
        "mode": "window",
        "block_records": 1000,
        "target_records": 1000,
        "projected_fields": WIKI_PROJECTION,
        "window": V2,
    }
    values.update(changes)
    return SamplingRequest(**values)


class RecordingOpener:
    """Offline opener: serves local bytes, records requests, optional fault."""

    def __init__(self, payloads: dict[str, bytes], fail_after: int | None = None) -> None:
        self.payloads = payloads
        self.requests: list[tuple[int, int]] = []
        self.fail_after = fail_after

    def open(self, request: Any, *, timeout: float) -> Any:
        name = urllib.parse.urlparse(request.full_url).path.rsplit("/", 1)[-1]
        payload = self.payloads[name]
        headers = dict(request.header_items())
        if self.fail_after is not None and len(self.requests) >= self.fail_after:
            raise RuntimeError("authored fault: connection torn down mid-selection")
        if "Range" not in headers:
            raise AssertionError("whole-file request issued by window acquisition")
        start_s, end_s = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start_s), int(end_s)
        self.requests.append((start, end))
        total = len(payload)

        class Part(io.BytesIO):
            status = 206
            headers = {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(end - start + 1),
                "ETag": '"authored-v1"',
            }

        return Part(payload[start : end + 1])


def _plan(
    file: str,
    ranges: dict[str, tuple[int, int]],
    *,
    window: ParquetWindowDecode | None = V2,
    projection: tuple[str, ...] | None = WIKI_PROJECTION,
    attempt: int = 1,
    **limit_changes: Any,
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_requests=100, max_retries=0, max_workers=1, overall_deadline_seconds=120
    )
    if limit_changes:
        limits = AcquisitionLimits.model_validate({**limits.model_dump(), **limit_changes})
    version = window.policy_version if window else 0
    plan = AcquisitionPlan(
        plan_id=f"wiki_window_v{version}_{attempt}",
        source_id="nemotron_specialized",
        view_id="Nemotron-Pretraining-Wiki-Rewrite",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision=REVISION,
        mode="selected_records",
        selected_files=[file],
        row_ranges=ranges,
        output_artifact_id="wiki_window",
        limits=limits,
        attempt=attempt,
        projected_fields=list(projection) if projection is not None else None,
        parquet_window=window,
    )
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="authored offline test",
                authorized_at="fixture",
                is_pilot_approved=True,
            )
        }
    )


def _run(plan: AcquisitionPlan, root: Path, opener: RecordingOpener) -> BoundedFetcher:
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    fetcher.opener = opener  # type: ignore[assignment]
    try:
        fetcher.run()
    finally:
        fetcher.close()
    return fetcher


def _records(root: Path) -> list[dict[str, Any]]:
    lines = (root / "output" / "selected_records.jsonl").read_bytes().splitlines()
    return [json.loads(line) for line in lines]


def _leaf_spans(path: Path, names: set[str]) -> list[tuple[int, int]]:
    group = pq.ParquetFile(path).metadata.row_group(0)
    spans = []
    for index in range(group.num_columns):
        column = group.column(index)
        if column.path_in_schema in names:
            start = column.data_page_offset
            if column.dictionary_page_offset is not None:
                start = min(start, column.dictionary_page_offset)
            spans.append((start, start + column.total_compressed_size))
    return spans


def _sha_start(seed: int, file: str, group: int, version: int, modulus: int) -> int:
    text = "|".join(
        [
            str(seed),
            "authored",
            "Nemotron-Pretraining-Wiki-Rewrite",
            REVISION,
            file,
            str(group),
            f"window-v{version}",
            "start",
        ]
    )
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % modulus


# ----------------------------------------------------------------- resolver


def test_pyarrow_leaf_model_for_structs(tmp_path: Path) -> None:
    path = _small_file(tmp_path / "s.parquet", groups=1, rows=10)
    parquet = pq.ParquetFile(path)
    # Parquet leaf names are NOT logical names: 'metadata' is not a leaf.
    assert "metadata" not in parquet.schema.names
    assert "metadata" in parquet.schema_arrow.names
    paths = [parquet.schema.column(i).path for i in range(len(parquet.schema))]
    assert paths[2:4] == ["metadata.category", "metadata.models_used"]
    layout = _layout(path)[path.name]
    by_name = {entry.name: entry for entry in layout.field_leaves}
    assert by_name["metadata"].leaf_indices == (2, 3)
    assert by_name["provenance"].leaf_indices == (4, 5)
    assert by_name["uuid"].leaf_indices == (6,)
    assert by_name["tags"].unsupported is not None


def test_resolver_semantics() -> None:
    fields = [
        ("a", pa.string()),
        ("s", pa.struct([("x", pa.int64()), ("y", pa.struct([("z", pa.string())]))])),
        ("l", pa.list_(pa.string())),
        ("m", pa.map_(pa.string(), pa.int64())),
    ]
    leaves = [
        LeafSpec(0, "a", 0),
        LeafSpec(1, "s.x", 0),
        LeafSpec(2, "s.y.z", 0),
        LeafSpec(3, "l.list.element", 1),
        LeafSpec(4, "m.key_value.key", 1),
        LeafSpec(5, "m.key_value.value", 1),
    ]
    mapping = map_fields_to_leaves(fields, leaves)
    resolved = resolve_projection(mapping, ["s", "a", "s"])
    assert resolved.logical_fields == ("s", "a")
    assert resolved.leaf_indices == (0, 1, 2)
    assert resolved.leaf_owner == {0: "a", 1: "s", 2: "s"}
    with pytest.raises(ProjectionRefusal, match="not present"):
        resolve_projection(mapping, ["nope"])
    with pytest.raises(ProjectionRefusal, match="l: list"):
        resolve_projection(mapping, ["l"])
    with pytest.raises(ProjectionRefusal, match="m: map"):
        resolve_projection(mapping, ["m"])
    # Count, path and duplicate-name conflicts all fail closed.
    with pytest.raises(ProjectionRefusal, match="mismatch"):
        map_fields_to_leaves(fields, leaves[:-1])
    swapped = [leaves[0], LeafSpec(1, "t.x", 0), *leaves[2:]]
    with pytest.raises(ProjectionRefusal, match="conflict"):
        map_fields_to_leaves(fields, swapped)
    with pytest.raises(ProjectionRefusal, match="duplicate"):
        map_fields_to_leaves([("a", pa.string()), ("a", pa.string())], leaves[:2])
    # A struct wrapping a list is refused, not silently treated as a struct.
    nested_list = [("s", pa.struct([("x", pa.list_(pa.int64()))]))]
    mapped = map_fields_to_leaves(nested_list, [LeafSpec(0, "s.x.list.element", 1)])
    with pytest.raises(ProjectionRefusal, match="struct child 'x': list"):
        resolve_projection(mapped, ["s"])


# ----------------------------------------------------------------- identity


def test_real_synth_v1_plan_hash_pinned() -> None:
    plan = load_acquisition_plan(FIXTURES / "plan.json")
    assert plan.plan_id == "plan_synth_default_huggingface_2353ec572c800576ff82"
    expected = "dd248136990f343fa086c674531b9fd61e1a9713bc2f4dcc3e5ff38dd0178c33"
    assert plan.plan_hash == plan.compute_behavioral_hash() == expected
    assert plan.authorization is not None
    assert plan.authorization.authorization_hash == expected
    assert plan.compute_selection_hash() == (
        "83c2bb2786f0709ef06ee4610587e8cdef838e32aa61d78debd3d7c144917caf"
    )
    assert plan.parquet_window is not None and plan.parquet_window.policy_version == 1


def test_v1_evidence_shape_unchanged(tmp_path: Path) -> None:
    """A fresh v1 report has exactly the key sets of the real SYNTH evidence."""
    real = json.loads((FIXTURES / "rows.evidence.json").read_text(encoding="utf-8"))
    table = pa.table({"a": [f"v{_hex(i, 'a')}" for i in range(3000)], "b": list(range(3000))})
    path = tmp_path / "flat.parquet"
    pq.write_table(table, path, row_group_size=3000)
    report = plan_sample_blocks(
        _layout(path), _request((path.name,), window=V1, projected_fields=("a", "b"))
    ).to_report()
    report["discovery"], report["metadata_budgets"] = "local", {}
    assert set(report) == set(real)
    assert set(report["windows"][0]) == set(real["windows"][0])
    assert set(report["window_policy"]) == set(real["window_policy"])
    assert report["window_policy"]["policy_version"] == 1


def test_v2_binds_behavior_and_differs_from_v1() -> None:
    ranges = {"part_000003.parquet": (10, 1010)}
    v1_plan = _plan("part_000003.parquet", ranges, window=V1)
    v2_plan = _plan("part_000003.parquet", ranges, window=V2)
    assert v1_plan.compute_behavioral_hash() != v2_plan.compute_behavioral_hash()
    # Records are the same logical selection; only execution identity differs.
    assert v1_plan.compute_selection_hash() == v2_plan.compute_selection_hash()
    with pytest.raises(ValueError, match="policy_version"):
        ParquetWindowDecode.model_validate({**V2.model_dump(), "policy_version": 3})


# ----------------------------------------------------------------- sampling


def test_legacy_rowgroup_refuses_and_v1_refuses_nested(big_file: Path) -> None:
    layouts = _layout(big_file)
    with pytest.raises(SamplingRefusal, match="total_byte_size"):
        plan_sample_blocks(
            layouts,
            SamplingRequest(
                source_id="authored",
                view_id="v",
                revision=REVISION,
                files=(big_file.name,),
                seed=1,
            ),
        )
    with pytest.raises(
        SamplingRefusal, match=r"flat projected columns only; nested: \['metadata'\]"
    ):
        plan_sample_blocks(layouts, _request((big_file.name,), window=V1))


def test_v2_evidence_accounts_every_leaf_once(big_file: Path) -> None:
    layouts = _layout(big_file)
    result = plan_sample_blocks(layouts, _request((big_file.name,)))
    report = result.to_report()
    (window,) = report["windows"]
    leaves = window["projected_physical_leaves"]
    assert [(leaf["logical_field"], leaf["path"]) for leaf in leaves] == [
        ("text", "text"),
        ("license", "license"),
        ("metadata", "metadata.category"),
        ("metadata", "metadata.models_used"),
        ("uuid", "uuid"),
    ]
    assert report["projected_logical_fields"] == list(WIKI_PROJECTION)
    assert set(report["projected_physical_leaves"]) == {leaf["path"] for leaf in leaves}
    footer = pq.ParquetFile(big_file).metadata.row_group(0)
    sizes = {
        footer.column(i).path_in_schema: (
            footer.column(i).total_compressed_size,
            footer.column(i).total_uncompressed_size,
        )
        for i in range(footer.num_columns)
    }
    for leaf in leaves:
        assert (leaf["compressed_bytes"], leaf["uncompressed_bytes"]) == sizes[leaf["path"]]
    projected = {leaf["path"] for leaf in leaves}
    assert window["selected_compressed_bytes"] == sum(sizes[p][0] for p in projected)
    assert window["selected_uncompressed_bytes"] == sum(sizes[p][1] for p in projected)
    assert window["unselected_compressed_bytes"] == sum(sizes[p][0] for p in UNPROJECTED)
    assert window["largest_selected_chunk"]["path"] in projected
    assert report["window_policy"]["policy_version"] == 2
    assert window["eligibility_worst_case"]["expected_scan_rows"] == 16_384


def test_v2_window_start_is_versioned_and_deterministic(big_file: Path) -> None:
    layouts = _layout(big_file)
    first = plan_sample_blocks(layouts, _request((big_file.name,)))
    again = plan_sample_blocks(layouts, _request((big_file.name,)))
    assert first.to_report() == again.to_report()
    (chosen,) = first.windows
    expected = _sha_start(20260918, big_file.name, 0, 2, 16_384 - 1000 + 1)
    assert chosen.start_in_group == expected
    v1_start = _sha_start(20260918, big_file.name, 0, 1, 16_384 - 1000 + 1)
    assert expected != v1_start  # the policy version is part of the construction
    seeds = {
        json.dumps(plan_sample_blocks(layouts, _request((big_file.name,), seed=s)).row_ranges)
        for s in range(6)
    }
    assert len(seeds) > 1
    other = plan_sample_blocks(layouts, _request((big_file.name,), seed=7)).row_ranges
    assert other != first.row_ranges
    assert (
        _plan(big_file.name, dict(other)).compute_selection_hash()
        != _plan(big_file.name, dict(first.row_ranges)).compute_selection_hash()
    )


def test_remote_discovery_matches_local_mapping(big_file: Path) -> None:
    payload = big_file.read_bytes()

    def fetch(start: int, end: int) -> tuple[bytes, int]:
        return payload[start : end + 1], len(payload)

    remote = discover_layout_over_ranges(
        big_file.name, fetch, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
    )
    assert remote == _layout(big_file)[big_file.name]


@pytest.mark.parametrize(
    ("table_kwargs", "message"),
    [
        ({"metadata": None}, "projected fields not present: \\['metadata'\\]"),
        (
            {
                "metadata": pa.array(
                    [{"category": CATEGORY, "models_used": ["a"]}] * 1000,
                    type=pa.struct(
                        [("category", pa.string()), ("models_used", pa.list_(pa.string()))]
                    ),
                )
            },
            "struct child 'models_used': list",
        ),
    ],
)
def test_malformed_nested_schema_refused(
    tmp_path: Path, table_kwargs: dict[str, Any], message: str
) -> None:
    table = _wiki_table(1000)
    if table_kwargs.get("metadata", "keep") is None:
        table = table.drop_columns(["metadata"])
    else:
        table = table.set_column(
            table.schema.get_field_index("metadata"), "metadata", table_kwargs["metadata"]
        )
    path = tmp_path / "bad.parquet"
    pq.write_table(table, path, row_group_size=1000)
    with pytest.raises(SamplingRefusal, match=message):
        plan_sample_blocks(_layout(path), _request((path.name,)))
    # Fetch refuses too (a hand-written range cannot bypass the resolver).
    with pytest.raises(RecordLimitError, match="window-v2 projection refused"):
        _run(
            _plan(path.name, {path.name: (0, 10)}),
            tmp_path / "x",
            RecordingOpener({path.name: path.read_bytes()}),
        )


def test_struct_child_ratio_bomb_refused(tmp_path: Path) -> None:
    rows = 2000
    bomb = pa.array([{"category": CATEGORY, "models_used": "m" * 4000}] * rows, type=METADATA_TYPE)
    table = _wiki_table(rows, extra=False).set_column(2, "metadata", bomb)
    path = tmp_path / "bomb.parquet"
    pq.write_table(
        table,
        path,
        row_group_size=rows,
        compression="zstd",
        use_dictionary=["text", "license", "uuid", "metadata.category"],
    )
    with pytest.raises(SamplingRefusal, match="'metadata.models_used' exceeds decompression"):
        plan_sample_blocks(_layout(path), _request((path.name,)))
    with pytest.raises(RecordLimitError, match="'metadata.models_used' exceeds decompression"):
        _run(
            _plan(path.name, {path.name: (0, 10)}),
            tmp_path / "x",
            RecordingOpener({path.name: path.read_bytes()}),
        )


def test_oversized_struct_leaf_page_refused(tmp_path: Path) -> None:
    rows = 2000
    wide = pa.array(
        [{"category": CATEGORY, "models_used": _hex(i, "w") * 64} for i in range(rows)],
        type=METADATA_TYPE,
    )
    table = _wiki_table(rows, extra=False).set_column(2, "metadata", wide)
    path = tmp_path / "bigpage.parquet"
    pq.write_table(
        table, path, row_group_size=rows, compression="none", data_page_size=4 * 1024 * 1024
    )
    opener = RecordingOpener({path.name: path.read_bytes()})
    plan = _plan(
        path.name, {path.name: (0, 10)}, max_parser_bytes=512 * 1024, max_record_bytes=64 * 1024
    )
    with pytest.raises(ValueError, match="range exceeds parser bound"):
        _run(plan, tmp_path / "x", opener)
    assert all(end - start + 1 <= 512 * 1024 for start, end in opener.requests)


# -------------------------------------------------------------- acquisition


def test_v2_fetch_reconstructs_structs_and_skips_unprojected(
    big_file: Path, tmp_path: Path
) -> None:
    result = plan_sample_blocks(_layout(big_file), _request((big_file.name,)))
    (chosen,) = result.windows
    payload = big_file.read_bytes()
    opener = RecordingOpener({big_file.name: payload})
    plan = _plan(big_file.name, dict(result.row_ranges))
    fetcher = _run(plan, tmp_path, opener)
    state = fetcher.journal.state
    assert state.status == "COMPLETED"
    assert state.accounting.consumed["records_scanned"] == chosen.expected_scan_rows
    records = _records(tmp_path)
    assert len(records) == 1000
    # Reference: an independent full decode of the same rows.
    reference = (
        pq.read_table(big_file, columns=list(WIKI_PROJECTION))
        .slice(chosen.start_row, 1000)
        .to_pylist()
    )
    for got, want in zip(records, reference, strict=True):
        got.pop("_xlm_acquisition")
        assert got == want
        assert encode_record(got) == encode_record(want)
        assert set(got["metadata"]) == {"category", "models_used"}
    assert any(r["metadata"]["models_used"] is None for r in records)
    # No byte of an unprojected leaf (nested struct or list) is requested.
    data_requests = [
        (start, end) for start, end in opener.requests if start != 0 and end != len(payload) - 1
    ]
    for low, high in _leaf_spans(big_file, UNPROJECTED):
        assert all(end < low or start >= high for start, end in data_requests)
    assert state.transferred_bytes < chosen.selected_compressed_bytes
    assert AcquisitionVerifier(plan, tmp_path / "output", journal=fetcher.journal).verify()


def test_wiki_adapter_equivalence_on_v2_output(tmp_path: Path) -> None:
    """v2 window records adapt exactly like a legacy full (exact) decode."""
    path = _small_file(tmp_path / "part.parquet", extra=False)
    result = plan_sample_blocks(_layout(path), _request((path.name,)))
    ranges = dict(result.row_ranges)
    payloads = {path.name: path.read_bytes()}
    _run(_plan(path.name, ranges), tmp_path / "v2", RecordingOpener(payloads))
    _run(
        _plan(path.name, ranges, window=None, projection=None),
        tmp_path / "exact",
        RecordingOpener(payloads),
    )
    window_bytes = (tmp_path / "v2/output/selected_records.jsonl").read_bytes()
    exact_bytes = (tmp_path / "exact/output/selected_records.jsonl").read_bytes()
    window_records, exact_records = _records(tmp_path / "v2"), _records(tmp_path / "exact")
    for got, want in zip(window_records, exact_records, strict=True):
        assert got.pop("_xlm_acquisition")["row_index"] == want.pop("_xlm_acquisition")["row_index"]
        assert got == want
    assert len(window_bytes.splitlines()) == len(exact_bytes.splitlines()) == 1000
    adapter = WikiRewriteAdapter()
    for row, record in enumerate(window_records):
        doc = adapter.adapt(record, source_file=path.name, source_row=row, source_revision="r")
        twin = adapter.adapt(
            exact_records[row], source_file=path.name, source_row=row, source_revision="r"
        )
        assert doc.to_dict() == twin.to_dict()
        assert doc.source_metadata["category"] == CATEGORY


def test_adapter_contract_over_nested_edge_rows(tmp_path: Path) -> None:
    only_category = pa.struct([("category", pa.string())])
    cases: list[tuple[pa.Array, type[Exception] | None]] = [
        (pa.array([{"category": CATEGORY}] * 20, type=only_category), None),
        (
            pa.array([{"category": CATEGORY, "models_used": None}] * 20, type=METADATA_TYPE),
            None,
        ),
        (
            pa.array([{"category": None, "models_used": "m"}] * 20, type=METADATA_TYPE),
            MissingFieldError,
        ),
        (
            pa.array([{"category": "Other-Component", "models_used": "m"}] * 20, METADATA_TYPE),
            RecordRejectedError,
        ),
        (
            pa.array([{"models_used": "m"}] * 20, type=pa.struct([("models_used", pa.string())])),
            MissingFieldError,
        ),
    ]
    adapter = WikiRewriteAdapter()
    for index, (metadata, expected) in enumerate(cases):
        path = tmp_path / f"case{index}.parquet"
        table = _wiki_table(20, extra=False).set_column(2, "metadata", metadata)
        pq.write_table(table, path, row_group_size=20)
        root = tmp_path / f"run{index}"
        _run(
            _plan(path.name, {path.name: (3, 8)}),
            root,
            RecordingOpener({path.name: path.read_bytes()}),
        )
        record = _records(root)[0]
        record.pop("_xlm_acquisition")
        assert isinstance(record["metadata"], dict)
        if expected is None:
            doc = adapter.adapt(record, source_file="f", source_row=3, source_revision="r")
            assert "models_used" not in doc.source_metadata
        else:
            with pytest.raises(expected):
                adapter.adapt(record, source_file="f", source_row=3, source_revision="r")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_transferred_bytes": 96 * 1024}, "response-body byte allowance exhausted"),
        ({"max_requests": 5}, "request limit reached"),
        ({"max_decompressed_bytes": 128 * 1024}, "decompressed limit reached"),
    ],
)
def test_v2_budgets_fail_closed(
    big_file: Path, tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    result = plan_sample_blocks(_layout(big_file), _request((big_file.name,)))
    window = V2
    if "max_decompressed_bytes" in changes:
        window = V2.model_copy(update={"ratio_exempt_bytes": 64 * 1024})
    plan = _plan(big_file.name, dict(result.row_ranges), window=window, **changes)
    with pytest.raises(BudgetExhaustedError, match=message):
        _run(plan, tmp_path, RecordingOpener({big_file.name: big_file.read_bytes()}))
    assert not (tmp_path / "output/selected_records.jsonl").exists()


def test_v2_scan_bound_refuses_before_column_bytes(big_file: Path, tmp_path: Path) -> None:
    payload = big_file.read_bytes()
    opener = RecordingOpener({big_file.name: payload})
    with pytest.raises(RecordLimitError, match="window scan exceeds bound"):
        _run(_plan(big_file.name, {big_file.name: (60_000, 61_000)}), tmp_path, opener)
    footer_start = len(payload) - 256 * 1024
    assert all(start == 0 or start >= footer_start for start, _ in opener.requests)


def test_v2_retry_and_reuse_are_exact(big_file: Path, tmp_path: Path) -> None:
    result = plan_sample_blocks(_layout(big_file), _request((big_file.name,)))
    ranges = dict(result.row_ranges)
    payloads = {big_file.name: big_file.read_bytes()}
    _run(_plan(big_file.name, ranges), tmp_path / "clean", RecordingOpener(payloads))
    expected = (tmp_path / "clean/output/selected_records.jsonl").read_bytes()
    plan = _plan(big_file.name, ranges)
    with pytest.raises(RuntimeError, match="authored fault"):
        _run(plan, tmp_path / "retry", RecordingOpener(payloads, fail_after=6))
    _run(plan, tmp_path / "retry", RecordingOpener(payloads))
    assert (tmp_path / "retry/output/selected_records.jsonl").read_bytes() == expected
    again = RecordingOpener(payloads)
    _run(plan, tmp_path / "retry", again)
    assert again.requests == []
    renewed = _plan(big_file.name, ranges, attempt=2)
    _run(renewed, tmp_path / "renewed", RecordingOpener(payloads))
    assert (tmp_path / "renewed/output/selected_records.jsonl").read_bytes() == expected


# ----------------------------------------------------------------------- CLI


def test_cli_v2_sample_then_plan(big_file: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "catalog_id": "authored",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "authored",
                        "provider": "https",
                        "repository": "http://127.0.0.1:9/unused",
                        "revision": REVISION,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    common = [
        "sample-blocks",
        "--source",
        "authored",
        "--view",
        "Nemotron-Pretraining-Wiki-Rewrite",
        "--catalog",
        str(catalog),
        "--revision",
        REVISION,
        "--files",
        big_file.name,
        "--local-dir",
        str(big_file.parent),
        "--seed",
        "20260918",
        "--mode",
        "window",
        "--adapter-spec",
        "wiki_rewrite",
    ]
    refused = runner.invoke(data_app, [*common, "--output", str(tmp_path / "v1.json")])
    assert refused.exit_code == 1 and "nested: ['metadata']" in refused.output
    assert not (tmp_path / "v1.json").exists()
    rows = tmp_path / "rows.json"
    sampled = runner.invoke(
        data_app, [*common, "--window-policy-version", "2", "--output", str(rows)]
    )
    assert sampled.exit_code == 0, sampled.output
    assert "--parquet-window-policy-version 2" in sampled.output
    evidence = json.loads((tmp_path / "rows.evidence.json").read_text(encoding="utf-8"))
    assert evidence["window_policy"]["policy_version"] == 2
    assert evidence["projected_logical_fields"] == list(WIKI_PROJECTION)
    plan_path = tmp_path / "plan.json"
    planned = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--view",
            "Nemotron-Pretraining-Wiki-Rewrite",
            "--catalog",
            str(catalog),
            "--files",
            big_file.name,
            "--mode",
            "selected_records",
            "--row-ranges",
            str(rows),
            "--adapter-spec",
            "wiki_rewrite",
            "--seed",
            "20260918",
            "--parquet-window-scan-rows",
            "16384",
            "--parquet-window-buffer-bytes",
            str(4 * 1024 * 1024),
            "--parquet-window-batch-rows",
            "256",
            "--parquet-window-policy-version",
            "2",
            "--pilot-approved",
            "--output",
            str(plan_path),
        ],
    )
    assert planned.exit_code == 0, planned.output
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["parquet_window"]["policy_version"] == 2 and saved["is_pilot"] is True
    bad = runner.invoke(
        data_app, [*common, "--window-policy-version", "3", "--output", str(tmp_path / "x.json")]
    )
    assert bad.exit_code == 1 and "policy_version" in bad.output
