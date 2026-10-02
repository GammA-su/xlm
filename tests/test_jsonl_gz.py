"""Bounded .jsonl.gz decoding, local adaptation, the Common Pile v2 contract and the
prefix sampler, on authored fixtures and a loopback endpoint (offline)."""

from __future__ import annotations

import gzip
import hashlib
import http.server
import json
import socket
import threading
import urllib.parse
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition import jsonl_gz, source_local
from xlm.data.acquisition import jsonl_gz_sample as sampler
from xlm.data.acquisition import source_formats as formats
from xlm.data.acquisition.records import RecordLimitError
from xlm.data.adapters import common_pile_adapters as v2
from xlm.data.adapters import mix01_adapters as v1
from xlm.data.adapters.registry import ADAPTERS_BY_ID, adapter_code_modules
from xlm.data.sources import certified_evidence as ce

REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
BOUNDS = jsonl_gz.JsonlGzBounds(
    max_decoded_bytes=10 * 1024**2,
    max_line_bytes=1024**2,
    max_rows=10_000,
    max_decompression_ratio=50,
)
BALANCED = (
    "libretexts",
    "news",
    "oercommons",
    "pressbooks",
    "project_gutenberg",
    "public_domain_review",
)


def rows_bytes(rows: list[dict[str, Any]], newline_at_end: bool = True) -> bytes:
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    return (text + ("\n" if newline_at_end else "")).encode("utf-8")


def gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def decode_all(payload: bytes, bounds: jsonl_gz.JsonlGzBounds = BOUNDS) -> list[bytes]:
    decoder = jsonl_gz.GzipLineDecoder(bounds)
    lines: list[bytes] = []
    for start in range(0, len(payload), 7):  # tiny pieces: framing across boundaries
        lines += decoder.feed(payload[start : start + 7])
    return lines + decoder.finish()


def texts(n: int, prefix: str = "Authored prose row") -> list[dict[str, Any]]:
    return [{"text": f"{prefix} {i}: " + "word " * (i % 7 + 1)} for i in range(n)]


# ------------------------------------------------------------------ decoder


def test_multi_member_stream_and_missing_final_newline_decode_exactly() -> None:
    first, second = texts(3), texts(2, "Second member")
    payload = gz(rows_bytes(first)) + gz(rows_bytes(second, newline_at_end=False))
    lines = decode_all(payload)
    assert [json.loads(line) for line in lines] == first + second


@pytest.mark.parametrize("cut", [1, 10, 25, -9, -1])
def test_truncated_gzip_refuses(cut: int) -> None:
    payload = gz(rows_bytes(texts(50)))
    with pytest.raises(jsonl_gz.JsonlGzError, match="truncated|corrupt|no gzip"):
        decode_all(payload[:cut] if cut > 0 else payload[:cut])


def test_corrupt_body_and_crc_refuse() -> None:
    payload = bytearray(gz(rows_bytes(texts(50))))
    payload[len(payload) // 2] ^= 0xFF
    with pytest.raises(jsonl_gz.JsonlGzError, match="corrupt"):
        decode_all(bytes(payload))
    tail = bytearray(gz(rows_bytes(texts(5))))
    tail[-6] ^= 0x01  # CRC32 trailer
    with pytest.raises(jsonl_gz.JsonlGzError, match="corrupt"):
        decode_all(bytes(tail))


def test_trailing_non_gzip_bytes_refuse() -> None:
    with pytest.raises(jsonl_gz.JsonlGzError, match="not gzip"):
        decode_all(gz(rows_bytes(texts(3))) + b"garbage")


def test_expansion_bomb_is_refused_before_it_is_materialized() -> None:
    bomb = gz(b'{"text": "' + b"a" * (64 * 1024**2) + b'"}\n')
    assert len(bomb) < 200_000
    ratio = jsonl_gz.JsonlGzBounds(
        max_decoded_bytes=1024**3, max_line_bytes=1024**3, max_rows=10, max_decompression_ratio=20
    )
    with pytest.raises(jsonl_gz.JsonlGzError, match="decompressed bytes exceed"):
        decode_all(bomb, ratio)
    absolute = jsonl_gz.JsonlGzBounds(
        max_decoded_bytes=4 * 1024**2,
        max_line_bytes=1024**3,
        max_rows=10,
        max_decompression_ratio=10_000,
    )
    with pytest.raises(jsonl_gz.JsonlGzError, match="decompressed bytes exceed"):
        decode_all(bomb, absolute)


def test_overlong_line_refuses_with_and_without_newline() -> None:
    small = jsonl_gz.JsonlGzBounds(
        max_decoded_bytes=10**7, max_line_bytes=100, max_rows=10, max_decompression_ratio=100
    )
    with pytest.raises(jsonl_gz.JsonlGzError, match="exceeds 100 bytes"):
        decode_all(gz(rows_bytes([{"text": "x" * 200}])), small)
    with pytest.raises(jsonl_gz.JsonlGzError, match="exceeds 100 bytes"):
        decode_all(gz(b'{"text": "' + b"x" * 5000), small)


def test_row_bound() -> None:
    bounded = jsonl_gz.JsonlGzBounds(
        max_decoded_bytes=10**7, max_line_bytes=1000, max_rows=3, max_decompression_ratio=100
    )
    assert len(decode_all(gz(rows_bytes(texts(3))), bounded)) == 3
    with pytest.raises(jsonl_gz.JsonlGzError, match="row count exceeds 3"):
        decode_all(gz(rows_bytes(texts(4))), bounded)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b'{"text": "caf\xe9"}', "not valid UTF-8"),
        (b"   ", "blank"),
        (b'{"text": "a", "text": "b"}', "duplicate JSON key"),
        (b'["text"]', "not a JSON object"),
        (b'{"text": NaN}', "non-finite"),
        (b'{"text": ', "not JSON"),
    ],
)
def test_strict_record_parsing(line: bytes, message: str) -> None:
    with pytest.raises(jsonl_gz.JsonlGzError, match=message):
        jsonl_gz.parse_record(line, 0)


# -------------------------------------------------------- local adaptation


def limits(**over: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "max_decompression_ratio": 50.0,
        "max_parser_bytes": 32 * 1024**2,
        "max_rows_per_file": 10_000,
        "max_record_bytes": 1024**2,
        "max_decoded_bytes_per_file": 64 * 1024**2,
        "max_ledger_bytes": 1024**2,
        "max_canonical_bytes_per_file": 64 * 1024**2,
        "max_durable_bytes_per_file": 128 * 1024**2,
        "scratch_min_free_bytes": 0,
    }
    value.update(over)
    return value


def adapt(path: Path, out: Path, name: str, **over: Any) -> dict[str, Any]:
    payload = path.read_bytes()
    return source_local.adapt_source_file(
        path,
        out,
        source_file=name,
        source_id="common_pile",
        view_id="common_pile_prose",
        adapter_id="common_pile",
        repository="common-pile/comma_v0.1_training_dataset",
        revision=REVISION,
        plan_id="plan",
        plan_hash="h" * 64,
        selection_hash="s" * 64,
        identity={
            "etag": '"e"',
            "sha256": hashlib.sha256(payload).hexdigest(),
            "length": len(payload),
        },
        limits=limits(**over.pop("limits", {})),
        **over,
    )


def write_gz(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "source.jsonl.gz"
    path.write_bytes(gz(rows_bytes(rows)))
    return path


def test_local_adaptation_is_deterministic_and_records_empty_text(tmp_path: Path) -> None:
    rows = texts(20)
    rows[4] = {"text": ""}
    rows[9] = {"text": "  \n\t "}
    path = write_gz(tmp_path, rows)
    name = "news/news.chunk.00.jsonl.gz"
    first = adapt(path, tmp_path / "a", name)
    second = adapt(path, tmp_path / "b", name)
    for key in ("documents_sha256", "selected_records_sha256", "rejections_sha256"):
        assert first[key] == second[key]
    assert (first["rows"], first["file_rows"], first["row_range"]) == (20, 20, [0, 20])
    assert (first["documents"], first["rejected"]) == (18, 2)
    assert first["rejection_counts_by_code"] == {"CommonPileEmptyTextError": 2}
    assert first["row_groups"] == 0 and first["projected_compressed_bytes"] == path.stat().st_size
    documents = [
        json.loads(line)
        for line in (tmp_path / "a" / "documents.jsonl").read_text("utf-8").splitlines()
    ]
    assert [d["source_row"] for d in documents] == [i for i in range(20) if i not in (4, 9)]
    for document in documents:
        assert document["source_file"] == name and document["source_revision"] == REVISION
        assert document["source_metadata"]["upstream_component"] == "news"
        assert document["text"] == rows[document["source_row"]]["text"]
    summary = json.loads((tmp_path / "a" / "adaptation_summary.json").read_text("utf-8"))
    assert summary["input"]["kind"] == "verified_source_jsonl_gz"


def test_missing_or_mistyped_text_fails_the_file(tmp_path: Path) -> None:
    for bad in ({"title": "no text"}, {"text": None}, {"text": 7}):
        rows = texts(3) + [bad]
        path = write_gz(tmp_path, rows)
        out = tmp_path / f"out-{len(list(tmp_path.iterdir()))}"
        with pytest.raises(v1.MissingFieldError):
            adapt(path, out, "news/news.chunk.00.jsonl.gz")
        path.unlink()


def test_local_bounds_refuse_the_whole_file(tmp_path: Path) -> None:
    path = write_gz(tmp_path, texts(30))
    name = "news/news.chunk.00.jsonl.gz"
    with pytest.raises(source_local.SourceAdaptError, match="row count exceeds 10"):
        adapt(path, tmp_path / "rows", name, limits={"max_rows_per_file": 10})
    with pytest.raises(source_local.SourceAdaptError, match="decompressed bytes exceed"):
        adapt(path, tmp_path / "decoded", name, limits={"max_decoded_bytes_per_file": 100})
    with pytest.raises(source_local.SourceAdaptError, match="exceeds 20 bytes"):
        adapt(path, tmp_path / "line", name, limits={"max_record_bytes": 20})
    with pytest.raises(source_local.SourceAdaptError, match="covers the whole file"):
        adapt(path, tmp_path / "range", name, row_range=(0, 5))
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(source_local.SourceAdaptError, match="truncated|corrupt"):
        adapt(path, tmp_path / "trunc", name)
    with pytest.raises(source_local.SourceAdaptError, match="neither .parquet nor .jsonl.gz"):
        adapt(path, tmp_path / "fmt", "news/news.chunk.00.jsonl")


def test_reserved_locator_field_refuses(tmp_path: Path) -> None:
    path = write_gz(tmp_path, [{"text": "a", "_xlm_acquisition": {}}])
    with pytest.raises(source_local.SourceAdaptError, match="reserved acquisition locator"):
        adapt(path, tmp_path / "out", "news/news.chunk.00.jsonl.gz")


def test_formats_are_exact_suffixes() -> None:
    assert formats.source_format("a/b.parquet") == formats.PARQUET
    assert formats.source_format("a/b.jsonl.gz") == formats.JSONL_GZ
    assert formats.partial_name("f00001", "a/b.parquet") == "f00001.parquet.part"
    assert formats.partial_name("f00001", "a/b.jsonl.gz") == "f00001.jsonl.gz.part"
    for bad in ("a/b.jsonl", "a/b.gz", "a/b.parquet.tmp", "a/b.JSONL.GZ"):
        with pytest.raises(formats.SourceFormatError):
            formats.source_format(bad)


# ------------------------------------------------------------- adapter v2


def test_registry_serves_the_v2_contract_and_its_identity() -> None:
    assert ADAPTERS_BY_ID["common_pile"] is v2.CommonPileAdapter
    assert v1.ADAPTERS_BY_ID["common_pile"] is v1.CommonPileAdapter
    modules = [m.__name__ for m in adapter_code_modules("common_pile")]
    assert modules[-1] == "xlm.data.adapters.common_pile_adapters"
    identity = ce.adapter_code_identity("common_pile")
    assert "xlm.data.adapters.common_pile_adapters" in identity
    # Other adapters keep their identities: only Common Pile's changed.
    assert "xlm.data.adapters.common_pile_adapters" not in ce.adapter_code_identity(
        "simple_stories"
    )


@pytest.mark.parametrize("component", BALANCED)
def test_v2_accepts_valid_text_byte_identically_for_all_balanced_components(component: str) -> None:
    name = f"{component}/{component}.chunk.07.jsonl.gz"
    record = {"text": "  Verbatim\tprose, kept exactly.\n"}
    new = v2.CommonPileAdapter().adapt(
        record, source_file=name, source_row=3, source_revision=REVISION
    )
    old = v1.CommonPileAdapter().adapt(
        record, source_file=name, source_row=3, source_revision=REVISION
    )
    assert new.to_dict() == old.to_dict()
    assert new.text == record["text"]
    assert new.source_metadata["upstream_component"] == component
    assert (new.source_file, new.source_row, new.source_revision) == (name, 3, REVISION)


def test_v2_contract_branches() -> None:
    adapter = v2.CommonPileAdapter()
    name = "pressbooks/pressbooks.chunk.18.jsonl.gz"
    for blank in ("", " ", "\n\t "):
        with pytest.raises(v2.CommonPileEmptyTextError) as caught:
            adapter.adapt({"text": blank}, source_file=name, source_row=0, source_revision=REVISION)
        assert "pressbooks" in str(caught.value)
    for bad in ({}, {"text": None}, {"text": 3}, {"text": ["a"]}):
        with pytest.raises(v1.MissingFieldError):
            adapter.adapt(bad, source_file=name, source_row=0, source_revision=REVISION)
    with pytest.raises(v1.MissingFieldError):
        adapter.adapt(
            {"text": ""},
            source_file="componentless.jsonl.gz",
            source_row=0,
            source_revision=REVISION,
        )


# ----------------------------------------------------------------- sampler


@dataclass
class Remote:
    files: dict[str, bytes] = field(default_factory=dict)
    requests: list[str | None] = field(default_factory=list)
    revision: str = REVISION
    etags: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def etag(self, name: str) -> str:
        return self.etags.get(name) or '"' + hashlib.sha256(self.files[name]).hexdigest() + '"'


class RangeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state: Remote = self.server.state  # type: ignore[attr-defined]
        kind, _, name = urllib.parse.unquote(self.path).lstrip("/").partition("/")
        payload = state.files.get(name)
        if payload is None:
            self.send_error(404)
            return
        if kind == "resolve":
            self.send_response(302)
            port = self.server.server_port  # type: ignore[attr-defined]
            self.send_header(
                "Location", f"http://127.0.0.1:{port}/object/{urllib.parse.quote(name)}"
            )
            self.send_header("X-Repo-Commit", state.revision)
            self.send_header("X-Linked-Size", str(len(payload)))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        requested = self.headers.get("Range")
        with state.lock:
            state.requests.append(requested)
        first, _, last = str(requested).removeprefix("bytes=").partition("-")
        start, end = int(first), min(int(last), len(payload) - 1)
        body = payload[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        self.send_header("ETag", state.etag(name))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Remote, str]]:
    real = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        if address[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError("offline test attempted a non-loopback connection")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    service.daemon_threads = True
    state = Remote()
    service.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()


def sample_limits(**over: Any) -> sampler.SampleLimits:
    value: dict[str, Any] = {
        "max_bytes": 4 * 1024**2,
        "max_requests": 64,
        "chunk_bytes": 4096,
        "timeout_seconds": 5.0,
        "deadline_seconds": 30.0,
        "max_line_bytes": 1024**2,
    }
    value.update(over)
    return sampler.SampleLimits(**value)


def incompressible_rows(n: int) -> list[dict[str, Any]]:
    return [
        {"text": "".join(hashlib.sha256(f"{i}-{j}".encode()).hexdigest() for j in range(40))}
        for i in range(n)
    ]


def test_prefix_sample_reads_only_what_the_rows_need(remote: tuple[Remote, str]) -> None:
    state, base = remote
    name = "pressbooks/pressbooks.chunk.18.jsonl.gz"
    rows = incompressible_rows(400)
    state.files[name] = gz(rows_bytes(rows))
    sample = sampler.sample_prefix(
        f"{base}/resolve/{name}",
        source_file=name,
        rows=16,
        revision=REVISION,
        expected_total_bytes=len(state.files[name]),
        limits=sample_limits(),
    )
    assert [json.loads(line) for line in sample.lines] == rows[:16]
    assert sample.whole_file is False
    assert sample.transferred_bytes < len(state.files[name]) // 4
    assert sample.requests == 2 * len(state.requests)  # one redirect hop per range request
    assert state.requests[0] == "bytes=0-4095"
    receipt = sampler.sample_receipt(
        [sample],
        label="t",
        source_id="common_pile",
        repository="common-pile/comma_v0.1_training_dataset",
        revision=REVISION,
        adapter_id="common_pile",
        bindings={"component_allowlist_digest": "a" * 64},
        limits=sample_limits(),
    )
    rendered = json.dumps(receipt)
    assert rows[0]["text"] not in rendered
    entry = receipt["files"][0]
    assert entry["schema"] == {"key_sets": [["text"]], "text_types": ["str"]}
    assert entry["adapter"]["accepted"] == 16 and entry["row_indices"] == [0, 16]
    assert entry["identity"]["linked"]["X-Repo-Commit"] == REVISION


def test_small_file_is_read_whole_and_verified(remote: tuple[Remote, str]) -> None:
    state, base = remote
    name = "public_domain_review/public_domain_review.chunk.01.jsonl.gz"
    state.files[name] = gz(rows_bytes(texts(5)))
    sample = sampler.sample_prefix(
        f"{base}/resolve/{name}",
        source_file=name,
        rows=16,
        revision=REVISION,
        expected_total_bytes=len(state.files[name]),
        limits=sample_limits(),
    )
    assert sample.whole_file is True and len(sample.lines) == 5


def test_prefix_sample_refusals(remote: tuple[Remote, str]) -> None:
    state, base = remote
    name = "oercommons/oercommons.chunk.49.jsonl.gz"
    state.files[name] = gz(rows_bytes(incompressible_rows(200)))
    size = len(state.files[name])
    url = f"{base}/resolve/{name}"

    def run(**over: Any) -> sampler.PrefixSample:
        kwargs: dict[str, Any] = {
            "source_file": name,
            "rows": 50,
            "revision": REVISION,
            "expected_total_bytes": size,
            "limits": sample_limits(),
        }
        kwargs.update(over)
        return sampler.sample_prefix(url, **kwargs)

    with pytest.raises(sampler.PrefixSampleError, match="byte ceiling"):
        run(limits=sample_limits(max_bytes=8192))
    with pytest.raises(sampler.PrefixSampleError, match="request ceiling"):
        run(limits=sample_limits(max_requests=3))
    with pytest.raises(sampler.PrefixSampleError, match="differs from the inventory"):
        run(expected_total_bytes=size + 1)
    with pytest.raises(sampler.PrefixSampleError, match="resolved to revision"):
        run(revision="b" * 40)
    with pytest.raises(Exception, match="not in the allowlist|cleartext"):
        sampler.sample_prefix(
            f"http://example.com/resolve/{name}",
            source_file=name,
            rows=1,
            revision=REVISION,
            expected_total_bytes=size,
            limits=sample_limits(),
        )


def test_sample_refuses_a_changed_validator(
    remote: tuple[Remote, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, base = remote
    name = "news/news.chunk.09.jsonl.gz"
    state.files[name] = gz(rows_bytes(incompressible_rows(200)))
    calls = {"n": 0}
    real = RangeHandler.do_GET

    def flipping(self: RangeHandler) -> None:
        if "/object/" in self.path:
            calls["n"] += 1
            if calls["n"] == 2:
                state.etags[name] = '"changed"'
        real(self)

    monkeypatch.setattr(RangeHandler, "do_GET", flipping)
    with pytest.raises(sampler.PrefixSampleError, match="changed between requests"):
        sampler.sample_prefix(
            f"{base}/resolve/{name}",
            source_file=name,
            rows=100,
            revision=REVISION,
            expected_total_bytes=len(state.files[name]),
            limits=sample_limits(),
        )


def test_record_limit_error_is_not_swallowed(tmp_path: Path) -> None:
    # The locator plus a near-bound record is still bounded.
    path = write_gz(tmp_path, [{"text": "x" * 900}])
    with pytest.raises((RecordLimitError, source_local.SourceAdaptError)):
        adapt(
            path, tmp_path / "out", "news/news.chunk.00.jsonl.gz", limits={"max_record_bytes": 905}
        )


def test_zlib_raw_deflate_is_not_gzip() -> None:
    raw = zlib.compress(rows_bytes(texts(2)))
    with pytest.raises(jsonl_gz.JsonlGzError, match="not gzip"):
        decode_all(raw)
