"""NETWORK: bounded prefix sample of the first rows of a remote ``.jsonl.gz`` file.

A gzip stream can only be decoded from its start, so the first ``rows`` rows
are read with sequential ``Range`` requests from byte 0, decoded by the same
:class:`~xlm.data.acquisition.jsonl_gz.GzipLineDecoder` the production
transport uses, and the stream is abandoned as soon as they are complete. The
whole file is never downloaded unless it is shorter than what the rows need.

Every request is to the revision-pinned resolve URL through the host
allowlist; the strong ETag, the declared length and the resolved revision
(``X-Repo-Commit``) must stay identical across requests, so a sample can never
mix two versions of a file. Requests, response-body bytes, a byte ceiling, a
request ceiling and a deadline are enforced and accounted.

The result separates content from accounting: :class:`PrefixSample` carries
the raw lines (for storage outside the code checkout), while
:func:`sample_receipt` describes them by counts, lengths, key sets and SHA-256
digests only, never text.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xlm.data.acquisition import jsonl_gz
from xlm.data.adapters.mix01_adapters import AdapterError, RecordRejectedError
from xlm.data.adapters.registry import ADAPTERS_BY_ID
from xlm.data.adapters.rejections import serialize_document
from xlm.data.evidence_v2 import canonical as canonical_json
from xlm.data.sources.transport import SafeRedirectHandler, validate_host

_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_LINKED = ("X-Linked-ETag", "X-Linked-Size", "X-Repo-Commit", "X-Xet-Hash")
#: Compressed bytes fed to the decoder per step when locating row ends.
SLICE_BYTES = 4096
SAMPLE_KIND = "jsonl_gz_prefix_sample"
SAMPLE_VERSION = 1


class PrefixSampleError(RuntimeError):
    """A bound, identity or format rule of a prefix sample was violated."""


@dataclass(frozen=True)
class SampleLimits:
    """Ceilings of one file's prefix sample; every value is a hard refusal."""

    max_bytes: int
    max_requests: int
    chunk_bytes: int
    timeout_seconds: float
    deadline_seconds: float
    max_line_bytes: int
    max_decompression_ratio: float = 15.0

    def __post_init__(self) -> None:
        if (
            min(self.max_bytes, self.max_requests, self.chunk_bytes, self.max_line_bytes) < 1
            or self.chunk_bytes > self.max_bytes
            or self.timeout_seconds <= 0
            or self.deadline_seconds <= 0
        ):
            raise ValueError("prefix sample limits must be positive and admit one chunk")


@dataclass
class PrefixSample:
    """One file's sampled rows and the transfer that produced them."""

    source_file: str
    url: str
    rows_requested: int
    lines: list[bytes] = field(default_factory=list)
    #: Compressed bytes fed when each row's newline was decoded (SLICE_BYTES granularity).
    row_end_compressed: list[int] = field(default_factory=list)
    decoded_bytes: int = 0
    compressed_fed: int = 0
    whole_file: bool = False
    etag: str | None = None
    total_bytes: int | None = None
    linked: dict[str, str] = field(default_factory=dict)
    requests: int = 0
    redirects: int = 0
    transferred_bytes: int = 0
    seconds: float = 0.0


class _Redirects(SafeRedirectHandler):
    """Allowlisted redirects whose repository link headers are kept."""

    def __init__(self) -> None:
        super().__init__()
        self.hops = 0
        self.linked: dict[str, str] = {}

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        self.hops += 1
        for name in _LINKED:
            value = headers.get(name)
            if value:
                self.linked[name] = str(value)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


Opener = Callable[[urllib.request.Request, float, _Redirects], Any]


def _open(request: urllib.request.Request, timeout: float, redirects: _Redirects) -> Any:
    return urllib.request.build_opener(redirects).open(request, timeout=timeout)


def sample_prefix(
    url: str,
    *,
    source_file: str,
    rows: int,
    revision: str,
    expected_total_bytes: int,
    limits: SampleLimits,
    opener: Opener = _open,
    clock: Callable[[], float] = time.monotonic,
) -> PrefixSample:
    """Fetch and decode exactly the first ``rows`` rows of one remote ``.jsonl.gz``."""
    if rows < 1:
        raise PrefixSampleError("a prefix sample needs at least one row")
    validate_host(url)
    started = clock()
    sample = PrefixSample(source_file=source_file, url=url, rows_requested=rows)
    decoder = jsonl_gz.GzipLineDecoder(
        jsonl_gz.JsonlGzBounds(
            max_decoded_bytes=int(limits.max_bytes * limits.max_decompression_ratio),
            max_line_bytes=limits.max_line_bytes,
            # Rows decoded past the sample in the same slice are bounded, then dropped.
            max_rows=rows + jsonl_gz.PULL_BYTES,
            max_decompression_ratio=limits.max_decompression_ratio,
        )
    )
    offset = 0
    try:
        while len(sample.lines) < rows:
            if clock() - started > limits.deadline_seconds:
                raise PrefixSampleError(f"'{source_file}': prefix sample deadline exceeded")
            if sample.requests >= limits.max_requests:
                raise PrefixSampleError(f"'{source_file}': request ceiling reached")
            end = min(offset + limits.chunk_bytes, expected_total_bytes) - 1
            if offset > end:
                break
            if sample.transferred_bytes + (end - offset + 1) > limits.max_bytes:
                raise PrefixSampleError(f"'{source_file}': byte ceiling reached before {rows} rows")
            headers = {
                "User-Agent": "xlm-acquisition/2",
                "Accept-Encoding": "identity",
                "Range": f"bytes={offset}-{end}",
            }
            if sample.etag is not None:
                headers["If-Range"] = sample.etag
            redirects = _Redirects()
            sample.requests += 1
            try:
                response = opener(
                    urllib.request.Request(url, headers=headers), limits.timeout_seconds, redirects
                )
            finally:
                sample.requests += redirects.hops
                sample.redirects += redirects.hops
            with response:
                body = _checked_body(response, sample, redirects, offset, end, revision)
            if expected_total_bytes != sample.total_bytes:
                raise PrefixSampleError(
                    f"'{source_file}': remote length {sample.total_bytes} differs from the "
                    f"inventory's {expected_total_bytes}"
                )
            sample.transferred_bytes += len(body)
            for start in range(0, len(body), SLICE_BYTES):
                piece = body[start : start + SLICE_BYTES]
                sample.compressed_fed += len(piece)
                for line in decoder.feed(piece):
                    sample.lines.append(line)
                    sample.row_end_compressed.append(sample.compressed_fed)
                    if len(sample.lines) == rows:
                        break
                if len(sample.lines) == rows:
                    break
            offset = end + 1
            if offset >= expected_total_bytes and len(sample.lines) < rows:
                # The whole file was needed: its end must verify.
                for line in decoder.finish():
                    sample.lines.append(line)
                    sample.row_end_compressed.append(sample.compressed_fed)
                sample.whole_file = True
                break
    except jsonl_gz.JsonlGzError as exc:
        raise PrefixSampleError(f"'{source_file}': {exc}") from exc
    sample.decoded_bytes = decoder.counters.decoded_bytes
    sample.seconds = clock() - started
    for index, line in enumerate(sample.lines):
        jsonl_gz.parse_record(line, index)
    return sample


def _checked_body(
    response: Any,
    sample: PrefixSample,
    redirects: _Redirects,
    offset: int,
    end: int,
    revision: str,
) -> bytes:
    if response.status != 206:
        raise PrefixSampleError(f"'{sample.source_file}': expected 206, got {response.status}")
    etag = response.headers.get("ETag")
    if not etag or etag.startswith("W/"):
        raise PrefixSampleError(f"'{sample.source_file}' has no strong ETag")
    match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
    if not match or int(match[1]) != offset or int(match[2]) != end:
        raise PrefixSampleError(f"'{sample.source_file}': inconsistent Content-Range")
    total = int(match[3])
    linked = dict(redirects.linked)
    if sample.etag is None:
        commit = linked.get("X-Repo-Commit")
        if commit != revision:
            raise PrefixSampleError(
                f"'{sample.source_file}' resolved to revision {commit!r}, not {revision}"
            )
        size = linked.get("X-Linked-Size")
        if size is not None and size != str(total):
            raise PrefixSampleError(f"'{sample.source_file}': linked size differs")
        sample.etag, sample.total_bytes, sample.linked = etag, total, linked
    elif (etag, total) != (sample.etag, sample.total_bytes) or (
        linked.get("X-Repo-Commit", revision) != revision
    ):
        raise PrefixSampleError(f"'{sample.source_file}' changed between requests")
    body: bytes = response.read(end - offset + 2)
    if len(body) != end - offset + 1:
        raise PrefixSampleError(f"'{sample.source_file}': short or oversized range body")
    return bytes(body)


def _component(source_file: str) -> str:
    return source_file.split("/", 1)[0]


def sample_receipt(
    samples: Sequence[PrefixSample],
    *,
    label: str,
    source_id: str,
    repository: str,
    revision: str,
    adapter_id: str,
    bindings: Mapping[str, str],
    limits: SampleLimits,
) -> dict[str, Any]:
    """Text-free receipt: transfer accounting, row shapes, adapter outcomes and digests."""
    factory: Any = ADAPTERS_BY_ID[adapter_id]
    adapter = factory()
    files: list[dict[str, Any]] = []
    for sample in samples:
        rows: list[dict[str, Any]] = []
        documents = hashlib.sha256()
        accepted = canonical = 0
        codes: dict[str, int] = {}
        for index, line in enumerate(sample.lines):
            record = jsonl_gz.parse_record(line, index)
            text = record.get("text")
            entry: dict[str, Any] = {
                "row": index,
                "line_bytes": len(line),
                "line_sha256": hashlib.sha256(line).hexdigest(),
                "keys": sorted(record),
                "text_type": type(text).__name__,
                "text_utf8_bytes": len(text.encode("utf-8")) if isinstance(text, str) else None,
                "compressed_bytes_at_row_end": sample.row_end_compressed[index],
            }
            try:
                document = adapter.adapt(
                    record,
                    source_file=sample.source_file,
                    source_row=index,
                    source_revision=revision,
                )
            except RecordRejectedError as exc:
                codes[type(exc).__name__] = codes.get(type(exc).__name__, 0) + 1
                entry["outcome"] = type(exc).__name__
            except AdapterError as exc:
                raise PrefixSampleError(
                    f"'{sample.source_file}' row {index}: adapter cannot read the row "
                    f"({type(exc).__name__})"
                ) from exc
            else:
                line_out = serialize_document(document).encode("utf-8") + b"\n"
                documents.update(line_out)
                accepted += 1
                canonical += document.utf8_byte_count
                entry["outcome"] = "accepted"
                entry["document_sha256"] = hashlib.sha256(line_out).hexdigest()
                entry["doc_id"] = document.doc_id
                entry["upstream_component"] = document.source_metadata.get("upstream_component")
            rows.append(entry)
        files.append(
            {
                "component": _component(sample.source_file),
                "file": sample.source_file,
                "url": sample.url,
                "row_indices": [0, len(sample.lines)],
                "rows_requested": sample.rows_requested,
                "rows": len(sample.lines),
                "whole_file_read": sample.whole_file,
                "identity": {
                    "etag": sample.etag,
                    "total_bytes": sample.total_bytes,
                    "linked": dict(sorted(sample.linked.items())),
                },
                "transfer": {
                    "requests": sample.requests,
                    "redirects": sample.redirects,
                    "transferred_bytes": sample.transferred_bytes,
                    "compressed_bytes_decoded": sample.compressed_fed,
                    "decoded_bytes": sample.decoded_bytes,
                    "seconds": round(sample.seconds, 3),
                },
                "schema": {
                    "key_sets": [list(keys) for keys in sorted({tuple(r["keys"]) for r in rows})],
                    "text_types": sorted({r["text_type"] for r in rows}),
                },
                "adapter": {
                    "accepted": accepted,
                    "rejected": len(rows) - accepted,
                    "rejection_counts_by_code": dict(sorted(codes.items())),
                    "canonical_bytes": canonical,
                    "documents_sha256": documents.hexdigest(),
                },
                "rows_detail": rows,
            }
        )
    body: dict[str, Any] = {
        "kind": SAMPLE_KIND,
        "version": SAMPLE_VERSION,
        "label": label,
        "source_id": source_id,
        "repository": repository,
        "revision": revision,
        "adapter_id": adapter_id,
        "bindings": dict(sorted(bindings.items())),
        "limits": {
            "max_bytes_per_file": limits.max_bytes,
            "max_requests_per_file": limits.max_requests,
            "chunk_bytes": limits.chunk_bytes,
            "timeout_seconds": limits.timeout_seconds,
            "deadline_seconds": limits.deadline_seconds,
            "max_line_bytes": limits.max_line_bytes,
            "max_decompression_ratio": limits.max_decompression_ratio,
        },
        "totals": {
            "files": len(files),
            "requests": sum(f["transfer"]["requests"] for f in files),
            "transferred_bytes": sum(f["transfer"]["transferred_bytes"] for f in files),
            "rows": sum(f["rows"] for f in files),
        },
        "files": files,
        "content_policy": "no corpus text; rows are described by lengths, key sets and SHA-256",
    }
    body["digest"] = canonical_json.digest(body)
    return body
