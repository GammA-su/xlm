"""Minimal contract-compatible token shards adhering to C07."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import struct
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from filelock import FileLock

from xlm.core.contracts import CanonicalDocument, TokenShardManifest
from xlm.data.exclusion.gates import MembershipGate, screened_documents
from xlm.data.input_policy import V2_DOCUMENT_TOKENS, V2_READ_TOKENS, V2_RECORD_BYTES
from xlm.tokenizers.base import BaseTokenizer

if TYPE_CHECKING:
    from xlm.data.exclusion.selection import SelectionGate

#: C07 document-index schemas. v1 (implicit: counters without ``index_schema``) stores
#: every token's canonical byte span in ``offsets.jsonl``. v2 stores the identical
#: records without ``token_byte_spans``; the spans are an exact function of the token
#: IDs and the per-ID canonical byte lengths in ``TOKEN_BYTES_FILE`` (bound by SHA-256
#: in the counters, hence in the C05 attestation) and are re-derived on read.
INDEX_SCHEMA_V1 = "c07-offsets-v1"
INDEX_SCHEMA_V2 = "c07-offsets-v2"
INDEX_SCHEMAS = (INDEX_SCHEMA_V1, INDEX_SCHEMA_V2)
#: Little-endian uint16 canonical UTF-8 byte length of every token ID, in ID order.
TOKEN_BYTES_FILE = "token_bytes.u16"
#: A v2 table covers at most a uint16 vocabulary plus headroom for uint32 shards.
MAX_TOKEN_BYTES_TABLE = 2 * 1024**2


def token_byte_lengths(tokenizer: BaseTokenizer) -> bytes:
    """The v2 table: ``len(token_to_bytes(id_to_token(id)))`` for every ID, as ``<u2``.

    Exactly the per-token byte length :meth:`ByteLevelBPETokenizer.encode_with_offsets`
    accumulates into its spans (special tokens have length 0).
    """
    to_bytes = getattr(tokenizer, "token_to_bytes", None)
    if to_bytes is None:
        raise ValueError("index schema v2 requires a byte-level tokenizer")
    lengths = [len(to_bytes(tokenizer.id_to_token(i))) for i in range(tokenizer.actual_vocab_size)]
    if max(lengths, default=0) > 65535:
        raise ValueError("token byte length exceeds the uint16 table")
    table = struct.pack(f"<{len(lengths)}H", *lengths)
    if len(table) > MAX_TOKEN_BYTES_TABLE:
        raise ValueError("token byte table exceeds its bound")
    return table


def derived_byte_spans(token_ids: Any, lengths: npt.NDArray[np.int64]) -> list[list[int]]:
    """Half-open canonical byte spans of one document's tokens (v1 ``token_byte_spans``).

    Spans are contiguous from 0; BOS/EOS have length 0, so they become ``[0, 0]`` and
    ``[n, n]``. The result has the exact shape and types ``json.loads`` gives a v1 line.
    """
    ids = np.asarray(token_ids, dtype=np.int64)
    if ids.size and (int(ids.min()) < 0 or int(ids.max()) >= len(lengths)):
        raise ValueError("token ID outside the shard's token byte table")
    sizes = lengths[ids]
    ends = np.cumsum(sizes)
    spans: list[list[int]] = np.column_stack((ends - sizes, ends)).tolist()
    return spans


@dataclass(frozen=True)
class DocumentTokenRecord:
    """Token representation of an individual document."""

    doc_id: str
    source_id: str
    token_ids: list[int]
    byte_count: int
    token_count: int
    offsets: list[tuple[int, int]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TokenShardWriter:
    """Minimal streaming token shard writer adhering to C07."""

    def __init__(
        self,
        output_dir: Path,
        shard_id: str,
        source_id: str,
        tokenizer: BaseTokenizer,
        pool_hash: str = "p02_local_pool",
        max_output_bytes: int | None = None,
        batch_size: int = 1,
        c05_gate: MembershipGate | None = None,
        selection: SelectionGate | None = None,
    ) -> None:
        if selection is not None and (c05_gate is None or selection.gate is not c05_gate):
            raise ValueError("selected-membership shards require the same C05 gate")
        self.selection = selection
        self.output_dir = output_dir
        self.shard_id = shard_id
        self.source_id = source_id
        self.tokenizer = tokenizer
        self.pool_hash = pool_hash
        self.c05_gate = c05_gate
        if max_output_bytes is not None and max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.max_output_bytes = max_output_bytes
        if not 1 <= batch_size <= 512:
            raise ValueError("batch_size must be 1..512")
        self.batch_size = batch_size
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Select token dtype from actual vocabulary size
        if tokenizer.actual_vocab_size <= 65536:
            self.token_dtype = "uint16"
            self.pack_char = "<H"
            self.dtype_max = 65535
        else:
            self.token_dtype = "uint32"
            self.pack_char = "<I"
            self.dtype_max = 4294967295

    def write_documents(
        self,
        documents: Iterable[CanonicalDocument],
        add_special_tokens: bool = False,
    ) -> TokenShardManifest:
        """Publish immutable files, with flushed payloads and the manifest last.

        An interrupted publication can leave orphan payloads, but never a manifest
        pointing at a partially written file. Existing payloads are never replaced.
        POSIX directory entries are fsynced; Windows file contents are fsynced, but
        directory power-loss persistence is not claimed by this portable API.
        """
        with _token_stage(self.output_dir) as stage:
            screened = screened_documents(documents, self.c05_gate)
            manifest = self._write_documents(screened, add_special_tokens, stage)
        return manifest

    def _write_documents(
        self,
        documents: Iterable[CanonicalDocument],
        add_special_tokens: bool,
        directory: Path,
    ) -> TokenShardManifest:
        bin_path = directory / "tokens.bin"
        idx_path = directory / "offsets.jsonl"

        bin_hasher = hashlib.sha256()
        idx_hasher = hashlib.sha256()

        num_tokens = 0
        num_docs = 0
        total_covered_bytes = 0
        total_canonical_bytes = 0
        total_valid_targets = 0
        total_eos_tokens = 0
        bos_id = self.tokenizer.bos_token_id
        eos_id = self.tokenizer.eos_token_id
        written_bytes = 0

        def charge(size: int) -> None:
            nonlocal written_bytes
            written_bytes += size
            if self.max_output_bytes is not None and written_bytes > self.max_output_bytes:
                raise ValueError("token shard output byte limit exceeded")

        with (
            bin_path.open("wb") as bin_f,
            idx_path.open("wb") as idx_f,
        ):
            for doc, (token_ids, offsets) in self._encoded_documents(documents, add_special_tokens):
                selection_record: dict[str, Any] = {}
                if self.selection is not None:
                    from xlm.data.exclusion.policy import C05Error

                    # Exact counts are re-derived here; the allocation-crossing record
                    # keeps exactly its selected valid targets (a token prefix).
                    counted, chosen = self.selection.expect(doc, self.source_id)
                    if max(0, len(token_ids) - 1) != counted:
                        raise C05Error("exact token count drifted from the bound count artifact")
                    token_ids, offsets = token_ids[: chosen + 1], offsets[: chosen + 1]
                    selection_record = {
                        # A logical-component shard: the mixture source labels the record;
                        # the canonical source stays bound through c05_content.
                        "source_id": self.source_id,
                        "c05_canonical_source_id": doc.source_id,
                        "c05_selection": self.selection.digest,
                        "c05_counted_valid_targets": counted,
                        "c05_selected_valid_targets": chosen,
                    }
                doc_token_count = len(token_ids)

                # Preserve little-endian bytes and validation, but cross the
                # Python/file/hash boundary once per bounded block, not per ID.
                for start in range(0, doc_token_count, 4096):
                    block = token_ids[start : start + 4096]
                    for tid in block:
                        if tid < 0 or tid > self.dtype_max:
                            raise ValueError(
                                f"Token ID {tid} cannot fit in declared dtype {self.token_dtype}"
                            )
                    packed = struct.pack(f"<{len(block)}{self.pack_char[-1]}", *block)
                    charge(len(packed))
                    bin_f.write(packed)
                    bin_hasher.update(packed)

                # Calculate byte coverage
                doc_covered_bytes = sum(end - start for start, end in offsets if (end - start) > 0)
                total_covered_bytes += doc_covered_bytes
                total_canonical_bytes += doc.utf8_byte_count

                # Structural-token markers, recorded by position so a consumer can
                # exclude BOS from targets and attribute EOS to this document's source
                # without re-deriving them from the ID stream (C07).
                bos_positions = [i for i, tid in enumerate(token_ids) if tid == bos_id]
                eos_positions = [i for i, tid in enumerate(token_ids) if tid == eos_id]

                # Valid next-token targets contributed by this document: every token
                # except the first, which is context only and never a target here.
                doc_valid_targets = max(0, doc_token_count - 1)

                idx_record = {
                    "doc_id": doc.doc_id,
                    "source_id": doc.source_id,
                    "token_start": num_tokens,
                    "token_count": doc_token_count,
                    "byte_count": doc.utf8_byte_count,
                    "covered_bytes": doc_covered_bytes,
                    "token_byte_spans": offsets,
                    "lineage_id": doc.cluster_ids.get("duplicate_cluster", ""),
                    "split_group": doc.cluster_ids.get("split_group", ""),
                    "byte_start": total_canonical_bytes - doc.utf8_byte_count,
                    "byte_end": total_canonical_bytes,
                    "valid_targets": doc_valid_targets,
                    "bos_positions": bos_positions,
                    "eos_positions": eos_positions,
                    "split": doc.split,
                }
                if self.c05_gate is not None:
                    from xlm.data.evidence_v2.canonical import digest

                    idx_record["c05_content"] = digest(doc.to_dict())
                    idx_record["c05_receipt"] = self.c05_gate.receipt_digest
                    idx_record.update(selection_record)
                idx_line = (json.dumps(idx_record, ensure_ascii=False) + "\n").encode("utf-8")
                charge(len(idx_line))
                idx_f.write(idx_line)
                idx_hasher.update(idx_line)

                num_tokens += doc_token_count
                num_docs += 1
                total_valid_targets += doc_valid_targets
                total_eos_tokens += len(eos_positions)

            for output in (bin_f, idx_f):
                output.flush()
                os.fsync(output.fileno())

        coverage_ratio = (
            float(total_covered_bytes) / float(total_canonical_bytes)
            if total_canonical_bytes > 0
            else 1.0
        )
        coverage_ratio = min(1.0, max(0.0, coverage_ratio))

        manifest = TokenShardManifest(
            shard_id=self.shard_id,
            source_id=self.source_id,
            num_tokens=num_tokens,
            num_documents=num_docs,
            token_dtype=self.token_dtype,
            endianness="little",
            tokenizer_hash=self.tokenizer.fingerprint,
            pool_hash=self.pool_hash,
            checksum_sha256=bin_hasher.hexdigest(),
            offsets_checksum_sha256=idx_hasher.hexdigest(),
            byte_coverage_ratio=coverage_ratio,
        )

        manifest_path = directory / "shard_manifest.json"
        charge(
            len(json.dumps(manifest.to_dict(), indent=2).replace("\n", os.linesep).encode("utf-8"))
        )
        _write_synced(manifest_path, json.dumps(manifest.to_dict(), indent=2))

        # Exposure counters live beside the contract manifest rather than inside it,
        # so extending them never changes the frozen TokenShardManifest schema (C07).
        counters = {
            "shard_id": self.shard_id,
            "source_id": self.source_id,
            "num_tokens": num_tokens,
            "num_documents": num_docs,
            "valid_targets": total_valid_targets,
            "eos_tokens": total_eos_tokens,
            "content_tokens": num_tokens - total_eos_tokens,
            "canonical_bytes": total_canonical_bytes,
            "covered_bytes": total_covered_bytes,
            "token_dtype": self.token_dtype,
        }
        counter_text = json.dumps(counters, indent=2, sort_keys=True)
        charge(len(counter_text.replace("\n", os.linesep).encode("utf-8")))
        _write_synced(directory / "shard_counters.json", counter_text)
        if self.c05_gate is not None:
            proof = self.c05_gate.seal_token_shard(manifest.to_dict(), counters)
            proof_text = json.dumps(proof, sort_keys=True)
            charge(len(proof_text.encode()))
            _write_synced(directory / "c05-attestation.json", proof_text)
        return manifest

    def _encoded_documents(
        self,
        documents: Iterable[CanonicalDocument],
        special: bool,
    ) -> Iterator[tuple[CanonicalDocument, tuple[list[int], list[tuple[int, int]]]]]:
        if self.batch_size == 1:
            for doc in documents:
                yield doc, self.tokenizer.encode_with_offsets(doc.text, special)
            return
        pending: list[CanonicalDocument] = []
        byte_count = 0
        for doc in documents:
            if pending and (
                len(pending) >= self.batch_size or byte_count + doc.utf8_byte_count > 1024**2
            ):
                yield from zip(
                    pending,
                    self.tokenizer.batch_encode_with_offsets([d.text for d in pending], special),
                    strict=True,
                )
                pending.clear()
                byte_count = 0
            pending.append(doc)
            byte_count += doc.utf8_byte_count
        if pending:
            yield from zip(
                pending,
                self.tokenizer.batch_encode_with_offsets([d.text for d in pending], special),
                strict=True,
            )


@contextmanager
def _token_stage(directory: Path) -> Iterator[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    names = ("tokens.bin", "offsets.jsonl", "shard_counters.json", "shard_manifest.json")
    lock = directory.parent / f".{directory.name}.writer.lock"
    with FileLock(str(lock), timeout=10):
        if any((directory / name).exists() for name in (*names, "c05-attestation.json")):
            raise FileExistsError(f"Token shard already contains payloads: {directory}")
        with tempfile.TemporaryDirectory(prefix=".token-stage-", dir=directory) as temp:
            stage = Path(temp)
            yield stage
            if (stage / "c05-attestation.json").exists():
                os.replace(stage / "c05-attestation.json", directory / "c05-attestation.json")
            for name in names:
                os.replace(stage / name, directory / name)
                if name == "shard_counters.json":
                    _sync_directory(directory)
            _sync_directory(directory)
            _sync_directory(directory.parent)


def _write_synced(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class TokenShardReader:
    """Reader for verifying and reading token shards."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        manifest_path = self.directory / "shard_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Shard manifest not found: {manifest_path}")
        if manifest_path.stat().st_size > 8 * 1024**2:
            raise ValueError("shard manifest exceeds byte limit")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.manifest = TokenShardManifest(**data)
        self.pack_char = "<H" if self.manifest.token_dtype == "uint16" else "<I"
        self.token_bytes_size = 2 if self.manifest.token_dtype == "uint16" else 4
        self._schema: str | None = None
        self._byte_lengths: npt.NDArray[np.int64] | None = None

    @property
    def index_schema(self) -> str:
        """``c07-offsets-v1`` unless the counters declare a later index schema."""
        if self._schema is None:
            schema = self.counters.get("index_schema", INDEX_SCHEMA_V1)
            if schema not in INDEX_SCHEMAS:
                raise ValueError(f"unknown C07 index schema in shard '{self.manifest.shard_id}'")
            self._schema = str(schema)
        return self._schema

    def token_byte_table(self) -> npt.NDArray[np.int64]:
        """The v2 per-ID canonical byte lengths, verified against the counters' SHA-256."""
        if self.index_schema != INDEX_SCHEMA_V2:
            raise ValueError("only a v2 shard carries a token byte table")
        if self._byte_lengths is None:
            path = self.directory / TOKEN_BYTES_FILE
            if not path.is_file() or path.stat().st_size > MAX_TOKEN_BYTES_TABLE:
                raise FileNotFoundError("Missing or oversized shard token byte table")
            raw = path.read_bytes()
            if (
                not raw
                or len(raw) % 2
                or hashlib.sha256(raw).hexdigest() != self.counters.get("token_bytes_sha256")
            ):
                raise ValueError(f"Token byte table mismatch for shard '{self.manifest.shard_id}'")
            self._byte_lengths = np.frombuffer(raw, dtype="<u2").astype(np.int64)
        return self._byte_lengths

    def with_byte_spans(
        self, record: dict[str, Any], physical_start: int | None = None
    ) -> dict[str, Any]:
        """Return ``record`` with its v1 ``token_byte_spans``, re-derived for a v2 shard.

        v1 records (and records already carrying spans) are returned unchanged.
        ``physical_start`` is the record's ``tokens.bin`` start when a caller has
        rebased ``token_start`` (ordered views).
        """
        if self.index_schema == INDEX_SCHEMA_V1:
            return record
        if self.manifest.endianness != "little":
            raise ValueError("v2 span reconstruction requires little-endian token IDs")
        if "token_byte_spans" in record:
            raise ValueError("v2 index must not override derived byte spans")
        start = int(record["token_start"]) if physical_start is None else int(physical_start)
        count = int(record["token_count"])
        if (
            start < 0
            or not 1 <= count <= V2_DOCUMENT_TOKENS
            or start + count > self.manifest.num_tokens
        ):
            raise ValueError("document index entry lies outside tokens.bin")
        code = "<u2" if self.manifest.token_dtype == "uint16" else "<u4"
        ids = np.fromfile(
            self.directory / "tokens.bin",
            dtype=code,
            count=count,
            offset=start * self.token_bytes_size,
        )
        if len(ids) != count:
            raise ValueError("tokens.bin is shorter than its document index")
        self.validate_v2_ids(record, ids)
        record["token_byte_spans"] = derived_byte_spans(ids, self.token_byte_table())
        return record

    @property
    def index_record_bytes(self) -> int:
        return V2_RECORD_BYTES if self.index_schema == INDEX_SCHEMA_V2 else 8 * 1024**2

    def check_read_window(self, count: int) -> None:
        if self.index_schema == INDEX_SCHEMA_V2 and not 0 <= count <= V2_READ_TOKENS:
            raise ValueError("v2 token read exceeds bounded window")

    def validate_v2_ids(self, record: dict[str, Any], ids: Any) -> None:
        """Validate v2 coverage without building Python spans (streaming startup)."""
        lengths = self.token_byte_table()
        count = record.get("token_count")
        if type(count) is not int or not 1 <= count <= V2_DOCUMENT_TOKENS or len(ids) != count:
            raise ValueError("v2 document token ceiling or count mismatch")
        if "token_byte_spans" in record or int(ids.max()) >= len(lengths) or int(ids.min()) < 0:
            raise ValueError("invalid v2 token IDs or explicit spans")
        sizes = lengths[ids]
        covered = int(sizes.sum())
        nbytes = record.get("byte_count")
        if (
            type(nbytes) is not int
            or not 0 <= covered <= nbytes
            or record.get("covered_bytes") != covered
        ):
            raise ValueError("v2 canonical byte coverage mismatch")
        bos, eos = record.get("bos_positions"), record.get("eos_positions")
        if bos not in ([], [0]) or eos not in ([], [count - 1]):
            raise ValueError("v2 structural positions are not a protected token prefix")
        if (bos and sizes[0] != 0) or (eos and (sizes[-1] != 0 or covered != nbytes)):
            raise ValueError("v2 structural byte spans differ from v1 framing")

    def verify_integrity(self) -> None:
        """Verify binary and index files against manifest checksums."""
        bin_path = self.directory / "tokens.bin"
        idx_path = self.directory / "offsets.jsonl"

        if not bin_path.is_file() or not idx_path.is_file():
            raise FileNotFoundError("Missing shard tokens.bin or offsets.jsonl")
        if self.index_schema == INDEX_SCHEMA_V2:
            self._byte_lengths = None  # Re-read and re-verify, never a cached table.
            self.token_byte_table()

        with bin_path.open("rb") as stream:
            actual_bin_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_bin_hash != self.manifest.checksum_sha256:
            raise ValueError(
                f"Binary checksum mismatch for shard '{self.manifest.shard_id}': "
                f"expected {self.manifest.checksum_sha256}, got {actual_bin_hash}"
            )

        with idx_path.open("rb") as stream:
            actual_idx_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_idx_hash != self.manifest.offsets_checksum_sha256:
            raise ValueError(
                f"Index checksum mismatch for shard '{self.manifest.shard_id}': "
                f"expected {self.manifest.offsets_checksum_sha256}, got {actual_idx_hash}"
            )

        expected_size = self.manifest.num_tokens * self.token_bytes_size
        if bin_path.stat().st_size != expected_size:
            raise ValueError(
                f"Corrupted binary length: expected {expected_size} bytes for "
                f"{self.manifest.num_tokens} tokens, got {bin_path.stat().st_size}"
            )

    def read_tokens(self, start: int = 0, count: int | None = None) -> list[int]:
        """Read a slice of token IDs from the shard."""
        bin_path = self.directory / "tokens.bin"
        total_tokens = self.manifest.num_tokens
        if start < 0 or start > total_tokens:
            raise ValueError(f"Invalid start offset {start} for shard of {total_tokens} tokens")

        num_to_read = total_tokens - start if count is None else min(count, total_tokens - start)
        self.check_read_window(num_to_read)
        byte_start = start * self.token_bytes_size
        byte_len = num_to_read * self.token_bytes_size

        with bin_path.open("rb") as f:
            f.seek(byte_start)
            raw_bytes = f.read(byte_len)

        format_str = f"<{num_to_read}{'H' if self.manifest.token_dtype == 'uint16' else 'I'}"
        return list(struct.unpack(format_str, raw_bytes))

    @contextmanager
    def mmap_tokens(self) -> Iterator[memoryview]:
        """Expose the token array as a read-only memory map.

        Memory mapping is what lets a shard larger than RAM be read: the operating
        system pages in only the windows actually touched, so peak resident memory
        follows the access pattern rather than the shard size (C07).
        """
        bin_path = self.directory / "tokens.bin"
        with bin_path.open("rb") as handle:
            if self.manifest.num_tokens == 0:
                yield memoryview(b"")
                return
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                view = memoryview(mapped)
                try:
                    yield view
                finally:
                    view.release()

    def read_tokens_mmap(self, start: int = 0, count: int | None = None) -> list[int]:
        """Read a token slice through the memory map, without loading the whole shard."""
        total = self.manifest.num_tokens
        if start < 0 or start > total:
            raise ValueError(f"Invalid start offset {start} for shard of {total} tokens")
        num_to_read = total - start if count is None else min(count, total - start)
        self.check_read_window(num_to_read)
        if num_to_read <= 0:
            return []

        size = self.token_bytes_size
        code = "H" if self.manifest.token_dtype == "uint16" else "I"
        with self.mmap_tokens() as view:
            # unpack_from reads through the map without creating a slice. A slice
            # would keep an exported pointer alive and block the map from closing.
            return list(struct.unpack_from(f"<{num_to_read}{code}", view, start * size))

    def iter_document_offsets(self) -> Iterator[dict[str, Any]]:
        """Stream document index entries one at a time.

        The eager :meth:`read_document_offsets` stays for small shards and existing
        callers; this is the bounded path for a shard with many documents.
        """
        idx_path = self.directory / "offsets.jsonl"
        if self.index_schema == INDEX_SCHEMA_V2:
            with idx_path.open("rb") as stream:
                while raw := stream.readline(V2_RECORD_BYTES + 1):
                    if len(raw) > V2_RECORD_BYTES:
                        raise ValueError("v2 index record exceeds byte limit")
                    yield json.loads(raw)
            return
        with idx_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    @property
    def counters(self) -> dict[str, Any]:
        """Aggregate exposure counters written alongside the manifest.

        Returns an empty mapping for a shard written before counters existed, so a
        missing sidecar is visible as absence rather than as a fabricated zero total.
        """
        path = self.directory / "shard_counters.json"
        if not path.is_file():
            return {}
        if path.stat().st_size > 8 * 1024**2:
            raise ValueError("shard counters exceed byte limit")
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def read_document_offsets(self) -> list[dict[str, Any]]:
        """Read document index entries."""
        idx_path = self.directory / "offsets.jsonl"
        if self.index_schema == INDEX_SCHEMA_V2:
            if idx_path.stat().st_size > 8 * 1024**2:
                raise ValueError("large v2 indexes require iter_document_offsets")
            return list(self.iter_document_offsets())
        records: list[dict[str, Any]] = []
        with idx_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records
