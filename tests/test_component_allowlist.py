"""Offline tests for the write-once component allowlist (authored fixtures only).

Covers the generic record, the allowlist-filtered inventory freeze, the
planning gate and the operator CLI. No network, no corpus payload, no operator
data: listings come from an authored page fixture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import source_plan as planner
from xlm.data.sources import hf_inventory as hfi
from xlm.data.sources.certified_evidence import SourcePin

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "implementation" / "evidence" / "COMMON-PILE-PROSE-ALLOWLIST-AUDIT"
REPOSITORY = "common-pile/comma_v0.1_training_dataset"
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
SEED = 20260918
#: component -> (files, bytes per file); sizes differ per component on purpose.
LAYOUT = {"cccc": (3, 900), "libretexts": (3, 40), "news": (2, 30), "youtube": (2, 500)}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inventory_tool = _load("mix01_inventory")
cli = _load("mix01_source")


class _Pages:
    def __init__(self, page: hfi.TreePage) -> None:
        self.page = page

    def fetch(self, cursor: str | None) -> hfi.TreePage:
        assert cursor is None
        return self.page


def _listing(
    layout: dict[str, tuple[int, int]] | None = None, revision: str = REVISION
) -> dict[str, Any]:
    items = [
        hfi.PageItem(path=f"{c}/{c}.chunk.{i:02d}.jsonl.gz", size_bytes=size, oid=f"o{c}{i}")
        for c, (count, size) in sorted((layout or LAYOUT).items())
        for i in range(count)
    ]
    page = hfi.TreePage(items=tuple(items), next_cursor=None, page_revision=revision, raw_bytes=100)
    return hfi.collect_listing(
        repository=REPOSITORY,
        requested_revision=revision,
        source_id="common_pile",
        view_id="common_pile_prose",
        filt=hfi.normalize_filter(
            path_prefix="", extensions=(".jsonl.gz",), include_globs=(), exclude_globs=()
        ),
        limits=hfi.ListingLimits(max_pages=4, max_items=100, max_requests=8),
        fetcher=_Pages(page),
    )


def _matrix(**over: Any) -> dict[str, Any]:
    components = {
        "cccc": ("EXCLUDE_CONTENT_FIT", "EXCLUDE_PROVENANCE", "LOW"),
        "libretexts": ("FIT", "PER_DOCUMENT_OPEN_LICENSE", "HIGH"),
        "news": ("FIT", "MIXED_BUT_FILTERED_TO_OPEN", "MEDIUM"),
        "youtube": ("PARTIAL_FIT", "NEEDS_OPERATOR_REVIEW", "MEDIUM"),
    }
    matrix: dict[str, Any] = {
        "kind": allow.MATRIX_KIND,
        "version": allow.MATRIX_VERSION,
        "repository": REPOSITORY,
        "revision": REVISION,
        "audit": "authored fixture",
        "sources": {"card": {"url": "https://example.invalid/card"}},
        "components": {
            name: {
                "content_fit": fit,
                "license_class": lic,
                "provenance_confidence": conf,
                "evidence": ["card"],
            }
            for name, (fit, lic, conf) in components.items()
        },
    }
    matrix.update(over)
    return matrix


def _record(**over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "source_key": "common_pile",
        "component_id": "common_pile_prose",
        "listing": _listing(),
        "matrix": _matrix(),
        "matrix_sha256": "m" * 64,
        "included": ["news", "libretexts"],
        "accepted_flags": [],
        "operator": "op",
        "rationale": "authored fixture",
    }
    kwargs.update(over)
    return allow.build_allowlist(**kwargs)


def _freeze(listing: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        names, sizes = hfi.listing_to_freeze_inputs(listing)
        return dict(
            inventory_tool.freeze_inventory("common_pile", REPOSITORY, REVISION, SEED, names, sizes)
        )
    names, sizes = allow.filtered_freeze_inputs(record, listing)
    return dict(
        inventory_tool.freeze_inventory(
            "common_pile",
            REPOSITORY,
            REVISION,
            SEED,
            names,
            sizes,
            allowlist=allow.inventory_binding(record),
        )
    )


# ------------------------------------------------------------------- record


def test_universe_is_the_exact_first_path_segment_of_every_listed_file() -> None:
    universe = allow.listing_universe(_listing())
    assert universe == {
        "cccc": {"files": 3, "bytes": 2700},
        "libretexts": {"files": 3, "bytes": 120},
        "news": {"files": 2, "bytes": 60},
        "youtube": {"files": 2, "bytes": 1000},
    }


@pytest.mark.parametrize(
    "bad", ["", "news", "/news/a.gz", "news\\a.gz", "news//a.gz", "./news/a.gz", "news/../a.gz"]
)
def test_component_of_refuses_componentless_or_unsafe_paths(bad: str) -> None:
    with pytest.raises(allow.AllowlistError):
        allow.component_of(bad)


def test_record_partitions_the_universe_and_embeds_its_evidence() -> None:
    record = _record()
    assert record["included"] == ["libretexts", "news"]
    assert record["excluded"] == ["cccc", "youtube"]
    assert record["revision"] == REVISION and record["repository"] == REPOSITORY
    assert record["listing"]["digest"] == _listing()["digest"]
    assert sorted(record["evidence"]) == ["cccc", "libretexts", "news", "youtube"]
    assert record["evidence"]["news"]["license_class"] == "MIXED_BUT_FILTERED_TO_OPEN"
    assert record["evidence_sources"] == {"card": {"url": "https://example.invalid/card"}}
    assert record["accepted_flags"] == []
    assert record["digest"] == _record()["digest"]
    assert allow.check_allowlist(record, _listing()) == record


def test_flagged_components_need_exactly_their_explicit_acceptance() -> None:
    with pytest.raises(allow.AllowlistError, match="flagged \\['youtube'\\]"):
        _record(included=["news", "youtube"])
    record = _record(included=["news", "youtube"], accepted_flags=["youtube"])
    assert record["accepted_flags"] == ["youtube"]
    # A stale or extra acceptance is refused, never silently kept.
    with pytest.raises(allow.AllowlistError):
        _record(included=["news"], accepted_flags=["youtube"])
    with pytest.raises(allow.AllowlistError):
        _record(included=["news", "youtube"], accepted_flags=["youtube", "news"])


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"included": []}, "at least one component"),
        ({"included": ["news", "news"]}, "distinct"),
        ({"included": ["news", "pressbooks"]}, "not in the listed universe"),
        ({"operator": " "}, "named operator"),
        ({"rationale": ""}, "written rationale"),
        ({"matrix_sha256": ""}, "sha256"),
        ({"matrix": _matrix(revision="a" * 40)}, "another repository or revision"),
        ({"matrix": _matrix(kind="other")}, "evidence matrix"),
    ],
)
def test_record_refusals(over: dict[str, Any], message: str) -> None:
    with pytest.raises(allow.AllowlistError, match=message):
        _record(**over)


def test_matrix_must_cover_exactly_the_listed_components() -> None:
    short = _matrix()
    del short["components"]["cccc"]
    with pytest.raises(allow.AllowlistError, match="missing \\['cccc'\\]"):
        _record(matrix=short)
    extra = _matrix()
    extra["components"]["pressbooks"] = dict(extra["components"]["news"])
    with pytest.raises(allow.AllowlistError, match="not listed \\['pressbooks'\\]"):
        _record(matrix=extra)
    unknown = _matrix()
    unknown["components"]["news"]["license_class"] = "PROBABLY_FINE"
    with pytest.raises(allow.AllowlistError, match="unknown license class"):
        _record(matrix=unknown)
    uncited = _matrix()
    uncited["components"]["news"]["evidence"] = ["nowhere"]
    with pytest.raises(allow.AllowlistError, match="unknown sources"):
        _record(matrix=uncited)


def test_incomplete_listing_cannot_define_a_universe() -> None:
    listing = _listing()
    listing["completion_status"] = "partial"
    with pytest.raises(allow.AllowlistError):
        allow.listing_universe(listing)


def test_tampered_record_or_other_listing_refused() -> None:
    record = _record()
    tampered = copy.deepcopy(record)
    tampered["included"] = ["cccc", "libretexts", "news"]
    with pytest.raises(allow.AllowlistError, match="digest"):
        allow.check_allowlist(tampered, _listing())
    other = _listing({**LAYOUT, "news": (3, 30)})
    with pytest.raises(allow.AllowlistError, match="another listing"):
        allow.check_allowlist(record, other)


def test_write_once_reuses_identical_and_refuses_divergent(tmp_path: Path) -> None:
    path = tmp_path / "calib" / "component_allowlists" / "common_pile.json"
    assert allow.write_once(path, _record()) is True
    assert allow.write_once(path, _record()) is False
    with pytest.raises(allow.AllowlistError, match="already recorded"):
        allow.write_once(path, _record(included=["news"]))
    assert json.loads(path.read_text(encoding="utf-8")) == _record()


# ---------------------------------------------------------------- inventory


def test_filtered_inventory_is_the_discovery_order_restricted_and_binds_the_allowlist() -> None:
    listing, record = _listing(), _record()
    discovery = _freeze(listing, None)
    production = _freeze(listing, record)
    kept = [e["file"] for e in production["files"]]
    assert kept == [
        e["file"] for e in discovery["files"] if e["file"].split("/")[0] in {"news", "libretexts"}
    ]
    assert production["seed"] == discovery["seed"] == SEED
    assert production["revision"] == discovery["revision"] == REVISION
    assert production["component_allowlist"] == {
        "digest": record["digest"],
        "included": ["libretexts", "news"],
        "excluded": ["cccc", "youtube"],
    }
    # The planner's independent recomputation verifies the bound digest ...
    assert planner.check_inventory(production, "common_pile", REPOSITORY, REVISION) == kept
    # ... and the binding cannot be dropped or swapped without breaking it.
    unbound = {k: v for k, v in production.items() if k != "component_allowlist"}
    with pytest.raises(planner.PlanError, match="digest"):
        planner.check_inventory(unbound, "common_pile", REPOSITORY, REVISION)
    swapped = copy.deepcopy(production)
    swapped["component_allowlist"]["digest"] = "0" * 64
    with pytest.raises(planner.PlanError, match="digest"):
        planner.check_inventory(swapped, "common_pile", REPOSITORY, REVISION)


def test_inventory_without_allowlist_keeps_its_historical_digest() -> None:
    listing = _listing()
    names, sizes = hfi.listing_to_freeze_inputs(listing)
    payload = inventory_tool.freeze_inventory(
        "common_pile", REPOSITORY, REVISION, SEED, names, sizes
    )
    entries = payload["files"]
    historical = hashlib.sha256(
        (
            f"v1|common_pile|{REPOSITORY}|{REVISION}|{SEED}|{len(entries)}\n"
            + "".join(f"{e['order_key']} {e['size_bytes']} {e['file']}\n" for e in entries)
        ).encode("utf-8")
    ).hexdigest()
    assert "component_allowlist" not in payload
    assert payload["inventory_digest"] == historical == planner.inventory_digest(payload)


def test_inventory_binding_refuses_discovery_and_widened_inventories() -> None:
    listing, record = _listing(), _record()
    allow.check_inventory_binding(record, listing, _freeze(listing, record))
    with pytest.raises(allow.AllowlistError, match="does not bind"):
        allow.check_inventory_binding(record, listing, _freeze(listing, None))
    # A wider allowlist's inventory carries an excluded component.
    wider = _record(included=["news", "libretexts", "youtube"], accepted_flags=["youtube"])
    with pytest.raises(allow.AllowlistError, match="does not bind"):
        allow.check_inventory_binding(record, listing, _freeze(listing, wider))
    # Same binding block, but an excluded file smuggled in: refused on membership.
    smuggled = _freeze(listing, record)
    smuggled["files"].append(
        {"file": "cccc/cccc.chunk.00.jsonl.gz", "size_bytes": 900, "order_key": "f" * 64}
    )
    with pytest.raises(allow.AllowlistError, match="differ"):
        allow.check_inventory_binding(record, listing, smuggled)
    dropped = _freeze(listing, record)
    dropped["files"].pop()
    with pytest.raises(allow.AllowlistError, match="differ"):
        allow.check_inventory_binding(record, listing, dropped)


def test_freeze_cli_filters_by_allowlist(tmp_path: Path) -> None:
    listing = _listing()
    listing_path = tmp_path / "common_pile.discovery.listing.json"
    hfi.write_listing(listing_path, listing)
    record_path = tmp_path / "allowlist.json"
    allow.write_once(record_path, _record())
    out = tmp_path / "common_pile.inventory.json"
    base = ["freeze", "--source", "common_pile", "--repo", REPOSITORY, "--revision", REVISION]
    assert (
        inventory_tool.main(
            [
                *base,
                "--listing",
                str(listing_path),
                "--allowlist",
                str(record_path),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    frozen = json.loads(out.read_text(encoding="utf-8"))
    assert frozen == _freeze(listing, _record())
    assert {e["file"].split("/")[0] for e in frozen["files"]} == {"libretexts", "news"}
    # An allowlist needs the listing it filters; a tampered allowlist refuses.
    assert (
        inventory_tool.main(
            [*base, "--files", str(out), "--allowlist", str(record_path), "--output", str(out)]
        )
        == 1
    )
    bad = json.loads(record_path.read_text(encoding="utf-8"))
    bad["included"] = ["cccc"]
    record_path.write_text(json.dumps(bad), encoding="utf-8")
    assert (
        inventory_tool.main(
            [
                *base,
                "--listing",
                str(listing_path),
                "--allowlist",
                str(record_path),
                "--output",
                str(tmp_path / "x.json"),
            ]
        )
        == 1
    )


# --------------------------------------------------------------------- gate


PIN = SourcePin(
    source_id="common_pile",
    view_id="common_pile_prose",
    component_id="common_pile_prose",
    provider="huggingface",
    repository=REPOSITORY,
    revision=REVISION,
    adapter_id="common_pile",
)


def _gate_root(tmp_path: Path, record: dict[str, Any] | None) -> argparse.Namespace:
    listing = _listing()
    hfi.write_listing(tmp_path / "inventories" / "common_pile.discovery.listing.json", listing)
    if record is not None:
        allow.write_once(tmp_path / "calib" / "component_allowlists" / "common_pile.json", record)
    return argparse.Namespace(data_root=str(tmp_path), source_key="common_pile")


def test_gate_refuses_common_pile_without_an_allowlist(tmp_path: Path) -> None:
    args = _gate_root(tmp_path, None)
    with pytest.raises(cli.DriverError, match="needs the operator component allowlist"):
        cli.allowlist_gate(args, PIN, _freeze(_listing(), None))


def test_gate_accepts_only_the_allowlisted_inventory(tmp_path: Path) -> None:
    record = _record()
    args = _gate_root(tmp_path, record)
    assert cli.allowlist_gate(args, PIN, _freeze(_listing(), record)) == record["digest"]
    with pytest.raises(cli.DriverError, match="does not bind"):
        cli.allowlist_gate(args, PIN, _freeze(_listing(), None))
    moved = SourcePin(**{**PIN.as_dict(), "revision": "b" * 40})
    with pytest.raises(cli.DriverError, match="not bound to this source pin"):
        cli.allowlist_gate(args, moved, _freeze(_listing(), record))


def test_gate_leaves_other_sources_alone_but_refuses_stray_bindings(tmp_path: Path) -> None:
    args = argparse.Namespace(data_root=str(tmp_path), source_key="simple_stories")
    other = SourcePin(**{**PIN.as_dict(), "source_id": "simple_stories"})
    plain = _freeze(_listing(), None)
    assert cli.allowlist_gate(args, other, plain) is None
    with pytest.raises(cli.DriverError, match="takes none"):
        cli.allowlist_gate(args, other, _freeze(_listing(), _record()))


def test_common_pile_stays_blocked_for_every_production_command() -> None:
    with pytest.raises(cli.DriverError, match="license/provenance"):
        cli.spec_of("common_pile")
    assert "common_pile" not in cli.SOURCES


# ---------------------------------------------------------------------- CLI


def test_allowlist_cli_preview_record_show(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _gate_root(tmp_path, None)
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps(_matrix()), encoding="utf-8")
    common = [
        "--source-key",
        "common_pile",
        "--data-root",
        str(tmp_path),
        "--evidence-matrix",
        str(matrix),
    ]
    decide = ["--include", "news,libretexts", "--operator", "op", "--rationale", "fixture"]
    path = tmp_path / "calib" / "component_allowlists" / "common_pile.json"

    assert cli.main(["allowlist", "show", *common]) == 1
    assert "no component allowlist" in capsys.readouterr().err
    assert cli.main(["allowlist", "preview", *common, *decide]) == 0
    assert "nothing written" in capsys.readouterr().out and not path.exists()
    assert (
        cli.main(
            [
                "allowlist",
                "record",
                *common,
                "--include",
                "news,youtube",
                "--operator",
                "op",
                "--rationale",
                "r",
            ]
        )
        == 1
    )
    assert "flagged ['youtube']" in capsys.readouterr().err and not path.exists()
    assert cli.main(["allowlist", "record", *common, *decide]) == 0
    out = capsys.readouterr().out
    assert "recorded" in out and "ALLOWLIST DIGEST" in out
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["included"] == ["libretexts", "news"]
    assert recorded["evidence_matrix"]["sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    assert cli.main(["allowlist", "record", *common, *decide]) == 0
    assert "identical allowlist already recorded" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "allowlist",
                "record",
                *common,
                "--include",
                "news",
                "--operator",
                "op",
                "--rationale",
                "fixture",
            ]
        )
        == 1
    )
    assert "already recorded" in capsys.readouterr().err
    assert cli.main(["allowlist", "show", *common]) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == recorded["digest"]


def test_allowlist_cli_refuses_other_sources_and_unpinned_listings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            ["allowlist", "preview", "--source-key", "simple_stories", "--data-root", str(tmp_path)]
        )
        == 1
    )
    assert "takes no component allowlist" in capsys.readouterr().err
    hfi.write_listing(
        tmp_path / "inventories" / "common_pile.discovery.listing.json", _listing(revision="c" * 40)
    )
    assert (
        cli.main(
            ["allowlist", "preview", "--source-key", "common_pile", "--data-root", str(tmp_path)]
        )
        == 1
    )
    assert "pinned source revision" in capsys.readouterr().err


# ------------------------------------------------------- committed evidence


def test_committed_matrix_covers_exactly_the_pinned_component_universe() -> None:
    matrix = json.loads((EVIDENCE / "license-matrix.json").read_text(encoding="utf-8"))
    components = json.loads((EVIDENCE / "components.json").read_text(encoding="utf-8"))
    allow.check_matrix(matrix, REPOSITORY, REVISION)
    universe = [row["component"] for row in components["components"]]
    assert sorted(matrix["components"]) == universe
    assert components["component_count"] == len(universe) == 31
    assert components["revision"] == REVISION
    assert sum(row["files"] for row in components["components"]) == components["file_count"]
    assert matrix["legal_advice"] is False
    # The driver binds the committed matrix by default.
    assert cli.ALLOWLIST_REQUIRED["common_pile"] == EVIDENCE / "license-matrix.json"
