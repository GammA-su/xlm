"""Offline tests for the generic bounded HF inventory lister (no network)."""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from xlm.data.sources import hf_inventory as hfi

REPO = "HuggingFaceFW/finepdfs-edu"
REV = "9cfabe2127faca99b3d5c4dc6d1fcb397399ebde"
REV2 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SOURCE = "finepdfs_edu"
VIEW = "eng_Latn"


def _filt(
    prefix: str = "",
    exts: tuple[str, ...] = (),
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> hfi.ListingFilter:
    return hfi.normalize_filter(
        path_prefix=prefix, extensions=exts, include_globs=include, exclude_globs=exclude
    )


def _limits(**over: Any) -> hfi.ListingLimits:
    base: dict[str, Any] = {
        "max_pages": 16,
        "max_items": 100,
        "max_requests": 32,
        "max_metadata_bytes": 8 * 1024 * 1024,
        "max_retries": 2,
        "per_request_timeout_seconds": 5.0,
        "total_deadline_seconds": 30.0,
    }
    base.update(over)
    return hfi.ListingLimits(**base)


class FakeFetcher:
    """Authored page fixture; records cursors to prove no payload access."""

    def __init__(self, pages: dict[str | None, hfi.TreePage | Exception]) -> None:
        self.pages = pages
        self.requested: list[str | None] = []

    def fetch(self, cursor: str | None) -> hfi.TreePage:
        self.requested.append(cursor)
        outcome = self.pages.get(cursor, KeyError(f"unexpected cursor {cursor!r}"))
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, hfi.TreePage)
        return outcome


def _page(
    items: list[tuple[str, int | None, str | None]],
    cursor: str | None = None,
    revision: str | None = REV,
    raw: int = 100,
    total: int | None = None,
) -> hfi.TreePage:
    return hfi.TreePage(
        items=tuple(hfi.PageItem(path=p, size_bytes=s, oid=o) for p, s, o in items),
        next_cursor=cursor,
        page_revision=revision,
        total_declared=total,
        raw_bytes=raw,
    )


def _collect(
    fetcher: FakeFetcher,
    filt: hfi.ListingFilter | None = None,
    limits: hfi.ListingLimits | None = None,
    revision: str = REV,
) -> dict[str, Any]:
    return hfi.collect_listing(
        repository=REPO,
        requested_revision=revision,
        source_id=SOURCE,
        view_id=VIEW,
        filt=filt or _filt(),
        limits=limits or _limits(),
        fetcher=fetcher,
    )


def test_single_page_repository() -> None:
    fetcher = FakeFetcher({None: _page([("data/eng_Latn/train/a.parquet", 10, "o1")], cursor=None)})
    receipt = _collect(fetcher)
    assert receipt["item_count"] == 1
    assert receipt["total_declared_bytes"] == 10
    assert receipt["pagination_complete"] is True
    assert receipt["completion_status"] == "complete"
    hfi.verify_listing(receipt)


def test_multi_page_repository() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("b.parquet", 2, None)], cursor="c1"),
            "c1": _page([("a.parquet", 1, None)], cursor=None),
        }
    )
    receipt = _collect(fetcher)
    assert [e["path"] for e in receipt["files"]] == ["a.parquet", "b.parquet"]
    assert receipt["page_count"] == 2
    hfi.verify_listing(receipt)


def test_unusual_page_order_is_deterministic() -> None:
    items = [("z.parquet", 3, None), ("a.parquet", 1, None), ("m.parquet", 2, None)]
    first = _collect(FakeFetcher({None: _page(items, cursor=None)}))
    shuffled = [items[2], items[0], items[1]]
    second = _collect(FakeFetcher({None: _page(shuffled, cursor=None)}))
    assert first["digest"] == second["digest"]
    assert first["file_list_digest"] == second["file_list_digest"]
    assert [e["path"] for e in first["files"]] == ["a.parquet", "m.parquet", "z.parquet"]


def test_duplicate_path_refused() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("a.parquet", 1, None)], cursor="c1"),
            "c1": _page([("a.parquet", 1, None)], cursor=None),
        }
    )
    with pytest.raises(hfi.HfInventoryError, match="duplicate path"):
        _collect(fetcher)


def test_duplicate_conflicting_metadata_refused() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("a.parquet", 1, "o1")], cursor="c1"),
            "c1": _page([("a.parquet", 2, "o2")], cursor=None),
        }
    )
    with pytest.raises(hfi.HfInventoryError, match="duplicate path"):
        _collect(fetcher)


def test_pagination_loop_refused() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("a.parquet", 1, None)], cursor="c1"),
            "c1": _page([("b.parquet", 2, None)], cursor=None),
        }
    )
    # Poison the second page to point back to an already-seen cursor.
    fetcher.pages["c1"] = _page([("b.parquet", 2, None)], cursor="c1")
    with pytest.raises(hfi.HfInventoryError, match="loop"):
        _collect(fetcher)


def test_same_cursor_loop_refused() -> None:
    fetcher = FakeFetcher({None: _page([("a.parquet", 1, None)], cursor="c1")})
    # Simulate provider returning the requesting cursor itself.
    fetcher.pages["c1"] = hfi.TreePage(
        items=(hfi.PageItem(path="b.parquet", size_bytes=2),),
        next_cursor="c1",
        page_revision=REV,
        raw_bytes=10,
    )
    with pytest.raises(hfi.HfInventoryError, match="loop"):
        _collect(fetcher)


def test_missing_continuation_refused() -> None:
    fetcher = FakeFetcher(
        {None: _page([("a.parquet", 1, None)], cursor=None, total=5)},
    )
    with pytest.raises(hfi.HfInventoryError, match="truncat|total"):
        _collect(fetcher)


def test_revision_drift_refused() -> None:
    fetcher = FakeFetcher({None: _page([("a.parquet", 1, None)], revision=REV2)})
    with pytest.raises(hfi.HfInventoryError, match="revision"):
        _collect(fetcher)


def test_symbolic_revision_refused() -> None:
    with pytest.raises(hfi.HfInventoryError, match="40-hex"):
        _collect(FakeFetcher({None: _page([], cursor=None)}), revision="main")


def test_max_request_refusal() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("a.parquet", 1, None)], cursor="c1"),
            "c1": _page([("b.parquet", 2, None)], cursor=None),
        }
    )
    with pytest.raises(hfi.HfInventoryError, match="request ceiling"):
        _collect(fetcher, limits=_limits(max_requests=1, max_pages=16))


def test_max_page_refusal() -> None:
    fetcher = FakeFetcher(
        {
            None: _page([("a.parquet", 1, None)], cursor="c1"),
            "c1": _page([("b.parquet", 2, None)], cursor=None),
        }
    )
    with pytest.raises(hfi.HfInventoryError, match="[Pp]age ceiling"):
        _collect(fetcher, limits=_limits(max_pages=1))


def test_max_item_refusal() -> None:
    fetcher = FakeFetcher(
        {None: _page([("a.parquet", 1, None), ("b.parquet", 2, None)], cursor=None)}
    )
    with pytest.raises(hfi.HfInventoryError, match="[Ii]tem ceiling"):
        _collect(fetcher, limits=_limits(max_items=1))


def test_metadata_byte_refusal() -> None:
    fetcher = FakeFetcher({None: _page([("a.parquet", 1, None)], raw=10_000)})
    with pytest.raises(hfi.HfInventoryError, match="[Mm]etadata byte"):
        _collect(fetcher, limits=_limits(max_metadata_bytes=100))


def test_retry_ceiling() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, cursor: str | None) -> hfi.TreePage:
            self.calls += 1
            raise hfi.TransientFetchError("boom")

    with pytest.raises(hfi.HfInventoryError, match="after .* attempt"):
        hfi.collect_listing(
            repository=REPO,
            requested_revision=REV,
            source_id=SOURCE,
            view_id=VIEW,
            filt=_filt(),
            limits=_limits(max_retries=1, max_requests=10),
            fetcher=Flaky(),  # type: ignore[arg-type]
        )


def test_retry_then_success() -> None:
    class FlakyOnce:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, cursor: str | None) -> hfi.TreePage:
            self.calls += 1
            if self.calls == 1:
                raise hfi.TransientFetchError("boom")
            return _page([("a.parquet", 1, None)], cursor=None)

    receipt = hfi.collect_listing(
        repository=REPO,
        requested_revision=REV,
        source_id=SOURCE,
        view_id=VIEW,
        filt=_filt(),
        limits=_limits(max_retries=2),
        fetcher=FlakyOnce(),  # type: ignore[arg-type]
    )
    assert receipt["item_count"] == 1


@pytest.mark.parametrize(
    "bad",
    ["", "/abs.parquet", "a//b.parquet", "a/", ".", "a/./b.parquet", "a/../b.parquet"],
)
def test_invalid_paths_refused(bad: str) -> None:
    with pytest.raises(hfi.HfInventoryError, match="path"):
        hfi.check_path(bad)


@pytest.mark.parametrize("bad", ["../secret.parquet", "a/../../x.parquet", ".."])
def test_path_traversal_refused(bad: str) -> None:
    fetcher = FakeFetcher({None: _page([(bad, 1, None)], cursor=None)})
    with pytest.raises(hfi.HfInventoryError, match="[Tt]raversal|malformed|escapes"):
        _collect(fetcher)


def test_negative_and_huge_sizes_refused() -> None:
    with pytest.raises(hfi.HfInventoryError):
        hfi.check_size(-1, "a.parquet")
    with pytest.raises(hfi.HfInventoryError):
        hfi.check_size(hfi.MAX_DECLARED_FILE_BYTES + 1, "a.parquet")


def test_prefix_and_extension_filtering() -> None:
    filt = _filt(prefix="data/eng_Latn/", exts=(".parquet",))
    fetcher = FakeFetcher(
        {
            None: _page(
                [
                    ("data/eng_Latn/train/a.parquet", 5, None),
                    ("data/eng_Latn/train/b.txt", 6, None),
                    ("data/fra_Latn/train/c.parquet", 7, None),
                ],
                cursor=None,
            )
        }
    )
    receipt = _collect(fetcher, filt=filt)
    assert [e["path"] for e in receipt["files"]] == ["data/eng_Latn/train/a.parquet"]
    hfi.verify_listing(receipt)


def test_finepdfs_filter_excludes_non_parquet() -> None:
    filt = _filt(prefix="data/eng_Latn/", exts=(".parquet",))
    fetcher = FakeFetcher(
        {
            None: _page(
                [
                    ("data/eng_Latn/train/000_00083.parquet", 100, "o1"),
                    ("data/eng_Latn/train/000_00084.parquet", 200, "o2"),
                    ("README.md", 10, None),
                    (".gitattributes", 5, None),
                    ("scripts/prepare.py", 50, None),
                    ("data/deu_Latn/train/x.parquet", 300, None),
                    ("data/eng_Latn/metadata.json", 20, None),
                ],
                cursor=None,
            )
        }
    )
    receipt = _collect(fetcher, filt=filt)
    assert [e["path"] for e in receipt["files"]] == [
        "data/eng_Latn/train/000_00083.parquet",
        "data/eng_Latn/train/000_00084.parquet",
    ]
    assert receipt["total_declared_bytes"] == 300


def test_include_exclude_globs() -> None:
    filt = _filt(prefix="data/", include=("*.parquet",), exclude=("*/tmp_*",))
    fetcher = FakeFetcher(
        {
            None: _page(
                [
                    ("data/a.parquet", 1, None),
                    ("data/tmp_b.parquet", 2, None),
                    ("data/c.txt", 3, None),
                ],
                cursor=None,
            )
        }
    )
    receipt = _collect(fetcher, filt=filt)
    assert [e["path"] for e in receipt["files"]] == ["data/a.parquet"]


def test_listing_digest_deterministic() -> None:
    def _run() -> dict[str, Any]:
        return _collect(
            FakeFetcher(
                {None: _page([("b.parquet", 2, None), ("a.parquet", 1, None)], cursor=None)}
            )
        )

    assert _run()["digest"] == _run()["digest"]
    assert _run()["file_list_digest"] == _run()["file_list_digest"]


def test_different_revision_changes_digest() -> None:
    first = _collect(FakeFetcher({None: _page([("a.parquet", 1, None)], cursor=None)}))
    second = _collect(
        FakeFetcher({None: _page([("a.parquet", 1, None)], cursor=None, revision=REV2)}),
        revision=REV2,
    )
    assert first["digest"] != second["digest"]
    assert first["file_list_digest"] == second["file_list_digest"]


def test_different_filters_change_digest() -> None:
    base = [("data/eng_Latn/a.parquet", 1, None), ("data/eng_Latn/b.txt", 2, None)]
    unfiltered = _collect(FakeFetcher({None: _page(base, cursor=None)}), filt=_filt())
    filtered = _collect(
        FakeFetcher({None: _page(base, cursor=None)}), filt=_filt(exts=(".parquet",))
    )
    assert unfiltered["digest"] != filtered["digest"]


def test_same_listing_freezes_byte_identically_twice(tmp_path: Path) -> None:
    import importlib.util
    import sys

    receipt = _collect(
        FakeFetcher({None: _page([("b.parquet", 2, "o2"), ("a.parquet", 1, "o1")], cursor=None)})
    )
    listing_path = tmp_path / "listing.json"
    hfi.write_listing(listing_path, receipt)
    script = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory_hf", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    out1 = tmp_path / "inv1.json"
    out2 = tmp_path / "inv2.json"
    argv = ["freeze", "--source", SOURCE, "--repo", REPO, "--revision", REV]
    assert module.main([*argv, "--listing", str(listing_path), "--output", str(out1)]) == 0
    assert module.main([*argv, "--listing", str(listing_path), "--output", str(out2)]) == 0
    assert out1.read_bytes() == out2.read_bytes()
    # Deterministic ordering follows the existing freeze hash chain.
    payload = json.loads(out1.read_text(encoding="utf-8"))
    assert payload["inventory_version"] == 1
    assert payload["seed"] == 20260918


def test_incomplete_listing_cannot_freeze(tmp_path: Path) -> None:
    import importlib.util
    import sys

    receipt = _collect(FakeFetcher({None: _page([("a.parquet", 1, None)], cursor=None)}))
    broken = dict(receipt)
    broken["pagination_complete"] = False
    broken["completion_status"] = "incomplete"
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(hfi.HfInventoryError):
        hfi.listing_to_freeze_inputs(broken)
    script = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory_hf2", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    out = tmp_path / "inv.json"
    code = module.main(
        [
            "freeze",
            "--source",
            SOURCE,
            "--repo",
            REPO,
            "--revision",
            REV,
            "--listing",
            str(broken_path),
            "--output",
            str(out),
        ]
    )
    assert code == 1
    assert not out.is_file()


def test_legacy_freeze_still_works(tmp_path: Path) -> None:
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory_hf3", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    files = tmp_path / "files.txt"
    files.write_text("b.parquet\na.parquet\n", encoding="utf-8")
    out = tmp_path / "inv.json"
    assert (
        module.main(
            [
                "freeze",
                "--source",
                SOURCE,
                "--repo",
                REPO,
                "--revision",
                REV,
                "--files",
                str(files),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["file_count"] == 2


def test_no_corpus_payload_endpoint_allowed() -> None:
    with pytest.raises(hfi.HfInventoryError):
        hfi.assert_metadata_only("https://huggingface.co/datasets/a/b/resolve/main/x.parquet")
    with pytest.raises(hfi.HfInventoryError):
        hfi.assert_metadata_only("https://huggingface.co/datasets/a/b/raw/main/x.parquet")
    url = hfi.build_tree_url(repository=REPO, revision=REV, path_prefix="data/eng_Latn/")
    assert "/resolve/" not in url
    assert "/api/datasets/" in url and "/tree/" in url
    hfi.assert_metadata_only(url)


def test_verify_listing_cli_offline(tmp_path: Path) -> None:
    import importlib.util
    import sys

    receipt = _collect(FakeFetcher({None: _page([("a.parquet", 4, None)], cursor=None)}))
    path = tmp_path / "listing.json"
    hfi.write_listing(path, receipt)
    script = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory_hf4", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.main(["verify-listing", "--listing", str(path)]) == 0


def test_write_once_semantics(tmp_path: Path) -> None:
    receipt = _collect(FakeFetcher({None: _page([("a.parquet", 1, None)], cursor=None)}))
    path = tmp_path / "listing.json"
    hfi.write_listing(path, receipt)
    before = path.read_bytes()
    hfi.write_listing(path, receipt)
    assert path.read_bytes() == before
    other = _collect(FakeFetcher({None: _page([("b.parquet", 9, None)], cursor=None)}))
    with pytest.raises(hfi.HfInventoryError, match="refusing to overwrite"):
        hfi.write_listing(path, other)


def test_loopback_tree_fetch_is_metadata_only() -> None:
    served = [
        {"type": "file", "path": "data/eng_Latn/train/a.parquet", "size": 11, "oid": "o1"},
        {"type": "file", "path": "data/eng_Latn/train/b.parquet", "size": 22, "oid": "o2"},
        {"type": "directory", "path": "data/eng_Latn/train"},
        {"type": "file", "path": "README.md", "size": 5, "oid": "o3"},
    ]
    requested_paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requested_paths.append(self.path)
            assert "/resolve/" not in self.path
            assert "/api/datasets/" in self.path and "/tree/" in self.path
            body = json.dumps(served).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Repo-Commit", REV)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fetcher = hfi.HfTreeFetcher(
            repository=REPO,
            revision=REV,
            path_prefix="",
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=5.0,
        )
        filt = _filt(prefix="data/eng_Latn/", exts=(".parquet",))
        receipt = hfi.collect_listing(
            repository=REPO,
            requested_revision=REV,
            source_id=SOURCE,
            view_id=VIEW,
            filt=filt,
            limits=_limits(),
            fetcher=fetcher,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert [e["path"] for e in receipt["files"]] == [
        "data/eng_Latn/train/a.parquet",
        "data/eng_Latn/train/b.parquet",
    ]
    assert receipt["total_declared_bytes"] == 33
    assert requested_paths and all("/resolve/" not in p for p in requested_paths)
