"""Bounded, fail-closed decoding of gzip-compressed JSON Lines (``.jsonl.gz``).

One decoder serves the whole-file local transport (a verified local file read
to its end) and the bounded prefix sampler (an HTTP prefix read until enough
rows were seen). Both therefore frame and parse rows identically.

Rules, each a hard refusal (:class:`JsonlGzError`):

- the stream is one or more complete gzip members; a corrupt member, a CRC or
  length trailer mismatch, trailing non-gzip bytes, or a stream that ends
  inside a member (truncation) refuses; a prefix reader that stops early never
  claims the end of the stream;
- decompressed bytes are bounded absolutely and relative to the compressed
  input (expansion-bomb bound), and output is pulled in bounded slices, so a
  bomb is refused before it is materialized;
- a line (without its ``\\n``) is bounded; a line that grows past the bound
  without a newline refuses before it is buffered further;
- every line is strict UTF-8 holding one JSON object: no blank lines, no
  duplicate keys, no ``NaN``/``Infinity``, nothing but an object;
- the row count is bounded.

Row identity is the zero-based line index in the decompressed stream. No
record is ever repaired, skipped or reordered here.
"""

from __future__ import annotations

import json
import math
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

FORMAT = "jsonl.gz"
SUFFIX = ".jsonl.gz"
GZIP_MAGIC = b"\x1f\x8b"
#: Smallest well-formed gzip member: 10-byte header + empty deflate + 8-byte trailer.
MIN_GZIP_BYTES = 18
#: Compressed bytes fed per decompression step and decompressed bytes pulled per call.
FEED_BYTES = 64 * 1024
PULL_BYTES = 1024 * 1024


class JsonlGzError(ValueError):
    """A ``.jsonl.gz`` stream, line or record violates a bound or the format."""


@dataclass(frozen=True)
class JsonlGzBounds:
    """Hard ceilings of one decode. Every value is a refusal threshold, not a target."""

    max_decoded_bytes: int
    max_line_bytes: int
    max_rows: int
    max_decompression_ratio: float

    def __post_init__(self) -> None:
        if (
            min(self.max_decoded_bytes, self.max_line_bytes, self.max_rows) < 1
            or not self.max_decompression_ratio >= 1
        ):
            raise ValueError("jsonl.gz bounds must be positive and the ratio at least 1")


@dataclass
class DecodeCounters:
    """What one decode consumed and produced (counts only, never content)."""

    compressed_bytes: int = 0
    decoded_bytes: int = 0
    rows: int = 0
    members: int = 0
    max_line_bytes: int = 0


class GzipLineDecoder:
    """Incremental decoder: feed compressed bytes, receive complete lines."""

    def __init__(self, bounds: JsonlGzBounds, counters: DecodeCounters | None = None) -> None:
        self.bounds = bounds
        self.counters = counters if counters is not None else DecodeCounters()
        self._inflate: Any = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._member_open = False
        self._pending = bytearray()
        self._finished = False

    def _decoded_limit(self) -> int:
        relative = int(self.counters.compressed_bytes * self.bounds.max_decompression_ratio)
        return min(self.bounds.max_decoded_bytes, relative)

    def _lines(self, data: bytes) -> list[bytes]:
        out: list[bytes] = []
        self._pending += data
        while True:
            cut = self._pending.find(b"\n")
            if cut < 0:
                if len(self._pending) > self.bounds.max_line_bytes:
                    raise JsonlGzError(
                        f"line {self.counters.rows} exceeds {self.bounds.max_line_bytes} bytes"
                    )
                return out
            line = bytes(self._pending[:cut])
            del self._pending[: cut + 1]
            out.append(self._row(line))

    def _row(self, line: bytes) -> bytes:
        if len(line) > self.bounds.max_line_bytes:
            raise JsonlGzError(
                f"line {self.counters.rows} exceeds {self.bounds.max_line_bytes} bytes"
            )
        if self.counters.rows >= self.bounds.max_rows:
            raise JsonlGzError(f"row count exceeds {self.bounds.max_rows}")
        self.counters.rows += 1
        self.counters.max_line_bytes = max(self.counters.max_line_bytes, len(line))
        return line

    def _emit(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self.counters.decoded_bytes += len(data)
        if self.counters.decoded_bytes > self._decoded_limit():
            raise JsonlGzError(
                "decompressed bytes exceed their bound "
                f"({self.counters.decoded_bytes} decoded of {self.counters.compressed_bytes} "
                f"compressed; ratio {self.bounds.max_decompression_ratio}, "
                f"absolute {self.bounds.max_decoded_bytes})"
            )
        return self._lines(data)

    def feed(self, chunk: bytes) -> list[bytes]:
        """Decode ``chunk`` (any size); return the lines it completed, in order."""
        if self._finished:
            raise JsonlGzError("decoder already finished")
        out: list[bytes] = []
        view = memoryview(chunk)
        for offset in range(0, len(view), FEED_BYTES):
            piece = bytes(view[offset : offset + FEED_BYTES])
            self.counters.compressed_bytes += len(piece)
            while piece:
                if not self._member_open:
                    # A member starts here; its magic may straddle two fed pieces.
                    if not piece.startswith(GZIP_MAGIC[: len(piece)]):
                        raise JsonlGzError("stream is not gzip (or has trailing non-gzip bytes)")
                    self._member_open = True
                    self.counters.members += 1
                try:
                    data = self._inflate.decompress(piece, PULL_BYTES)
                    out += self._emit(data)
                    while self._inflate.unconsumed_tail:
                        data = self._inflate.decompress(self._inflate.unconsumed_tail, PULL_BYTES)
                        out += self._emit(data)
                except zlib.error as exc:
                    raise JsonlGzError(f"corrupt gzip stream: {exc}") from exc
                if self._inflate.eof:
                    piece = self._inflate.unused_data
                    self._inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    self._member_open = False
                else:
                    piece = b""
        return out

    def finish(self) -> list[bytes]:
        """The stream ended: refuse truncation; a final line without newline is a row."""
        if self._finished:
            raise JsonlGzError("decoder already finished")
        self._finished = True
        if self.counters.members == 0:
            raise JsonlGzError("stream holds no gzip member")
        if self._member_open:
            # Ending inside a member is truncation, whatever bytes it still held.
            raise JsonlGzError("gzip stream is truncated inside a member")
        if self._pending:
            line = bytes(self._pending)
            self._pending.clear()
            return [self._row(line)]
        return []


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise JsonlGzError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _no_constant(name: str) -> Any:
    raise JsonlGzError(f"non-finite JSON number {name}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise JsonlGzError("JSON number overflows to a non-finite value")
    return parsed


def parse_record(line: bytes, row: int) -> dict[str, Any]:
    """One strict JSON object from one line; refuses anything else."""
    if not line.strip():
        raise JsonlGzError(f"line {row} is blank")
    try:
        text = line.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise JsonlGzError(f"line {row} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
            parse_float=_finite_float,
        )
    except ValueError as exc:
        if isinstance(exc, JsonlGzError):
            raise
        raise JsonlGzError(f"line {row} is not JSON") from exc
    if not isinstance(value, dict):
        raise JsonlGzError(f"line {row} is not a JSON object")
    return value


def check_gzip_header(path: Path) -> None:
    """Cheap pre-check of a local file before any decode."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(2)
    if size < MIN_GZIP_BYTES or head != GZIP_MAGIC:
        raise JsonlGzError(f"'{path.name}' is not a gzip file")


def iter_records(
    stream: BinaryIO,
    bounds: JsonlGzBounds,
    counters: DecodeCounters | None = None,
    *,
    read_bytes: int = 1024 * 1024,
) -> Iterator[tuple[int, dict[str, Any], bytes]]:
    """``(row index, record, raw line)`` of a complete stream, in order, to its verified end."""
    decoder = GzipLineDecoder(bounds, counters)
    row = 0
    while chunk := stream.read(read_bytes):
        for line in decoder.feed(chunk):
            yield row, parse_record(line, row), line
            row += 1
    for line in decoder.finish():
        yield row, parse_record(line, row), line
        row += 1
