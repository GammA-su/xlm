"""Reversible Byte-Level BPE Tokenizer wrapping Hugging Face tokenizers complying with C06."""

from __future__ import annotations

import hashlib
import json
import struct
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from tokenizers import Encoding, Tokenizer
from tokenizers.decoders import ByteLevel as ByteLevelDecoder
from tokenizers.models import BPE
from tokenizers.pre_tokenizers import ByteLevel
from tokenizers.trainers import BpeTrainer

from xlm.core.contracts import CanonicalDocument
from xlm.data.exclusion.gates import MembershipGate, screened_documents
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.tokenizers.base import BaseTokenizer

SPECIAL_TOKENS = ["<pad>", "<bos>", "<eos>", "<unk>"]
MINIMUM_VOCAB_SIZE = 260  # 4 special tokens + 256 bytes


def _get_gpt2_bytes_to_unicode() -> dict[int, str]:
    """Generate exact GPT-2 byte-to-unicode bijection."""
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(2**8):
        if b not in bs:
            bs.append(b)
            cs.append(2**8 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs], strict=False))


_B2U = _get_gpt2_bytes_to_unicode()
_U2B = {v: k for k, v in _B2U.items()}


class FitSampleBoundError(ValueError):
    """A complete-sample fit received more documents or bytes than its frozen bounds."""


@contextmanager
def _fit_text_stream(
    documents: Iterable[CanonicalDocument],
    max_docs: int,
    max_bytes: int,
    *,
    require_complete: bool = False,
    spool_dir: Path | None = None,
    on_feed: Callable[[int, int], None] | None = None,
) -> Iterator[tuple[Iterator[str], str]]:
    """Validate selection before fitting; spool text without retaining the corpus.

    Length framing preserves embedded newlines/NULs and exact document boundaries.
    Scratch is canonical sample bytes plus eight bytes per selected document.
    The backend still owns its merge frontier; this only bounds Python preparation.

    By default the caps silently stop at the first document past them (historical
    behavior). ``require_complete`` makes the caps exact bounds of a frozen sample:
    exceeding one refuses instead of truncating, so every document is fed once.
    ``on_feed`` receives cumulative (documents, canonical bytes) as text is fed.
    """
    digest = hashlib.sha256()
    total_bytes = count = 0
    with tempfile.TemporaryFile(mode="w+b", dir=spool_dir) as spool:
        for doc in documents:
            if doc.split != "train":
                raise ValueError(
                    f"Contract violation: Document '{doc.doc_id}' belongs to split '{doc.split}', "
                    "not 'train'. Only 'train' split documents may be used for tokenizer fitting."
                )
            if count >= max_docs or total_bytes + doc.utf8_byte_count > max_bytes:
                if require_complete:
                    raise FitSampleBoundError(
                        "tokenizer-fit sample exceeds its frozen bounds; refusing to truncate"
                    )
                break
            write_fit_frame(spool, doc)
            if count:
                digest.update(b"\n")
            digest.update(fit_input_line(doc))
            total_bytes += doc.utf8_byte_count
            count += 1
        if not count:
            raise ValueError("No valid training documents provided for tokenizer training.")
        spool.seek(0)

        yield _read_frames(spool, on_feed), digest.hexdigest()


class ByteLevelBPETokenizer(BaseTokenizer):
    """Reversible Byte-Level BPE tokenizer with literal special-token protection.

    Adheres strictly to C06:
    - Byte-level vocabulary: initial 256 byte characters + 4 reserved special tokens.
    - Zero-loss byte-level reversibility.
    - Protection against literal text: strings like '<eos>' in ordinary text
      encode to normal byte/subword tokens, never to control IDs.
    - True half-open UTF-8 byte spans derived from token byte payloads.
    """

    def __init__(
        self,
        tokenizer: Tokenizer,
        target_vocab_size: int,
        training_input_hash: str | None = None,
        is_production_baseline: bool = False,
    ) -> None:
        self._tok = tokenizer
        self._target_vocab_size = target_vocab_size
        self._training_input_hash = training_input_hash or "unfitted"
        self._is_production_baseline = is_production_baseline

        pad_val = self.token_to_id("<pad>")
        self._pad_id: int = pad_val if pad_val is not None else 0

        bos_val = self.token_to_id("<bos>")
        self._bos_id: int = bos_val if bos_val is not None else 1

        eos_val = self.token_to_id("<eos>")
        self._eos_id: int = eos_val if eos_val is not None else 2

        unk_val = self.token_to_id("<unk>")
        self._unk_id: int = unk_val if unk_val is not None else 3

        # Key by token spelling, not ID: lengths remain valid if an externally
        # supplied backend changes its vocabulary. Keep memory bounded per instance.
        self._byte_lengths: dict[str, int] = {}

    @property
    def pad_token_id(self) -> int:
        return self._pad_id

    @property
    def bos_token_id(self) -> int:
        return self._bos_id

    @property
    def eos_token_id(self) -> int:
        return self._eos_id

    @property
    def unk_token_id(self) -> int:
        return self._unk_id

    @property
    def vocab_size(self) -> int:
        return self._target_vocab_size

    @property
    def actual_vocab_size(self) -> int:
        return self._tok.get_vocab_size()

    @property
    def is_production_baseline(self) -> bool:
        return self._is_production_baseline

    @property
    def fingerprint(self) -> str:
        data = (
            f"ByteLevelBPE:v1:target={self._target_vocab_size}:actual={self.actual_vocab_size}:"
            f"input_hash={self._training_input_hash}:prod={self._is_production_baseline}"
        )
        return compute_sha256(data)

    def id_to_token(self, token_id: int) -> str:
        res = self._tok.id_to_token(token_id)
        if res is None:
            raise ValueError(f"Token ID {token_id} not found in vocabulary")
        return res

    def token_to_id(self, token: str) -> int | None:
        return self._tok.token_to_id(token)

    def token_to_bytes(self, token: str) -> bytes:
        """Derive the true UTF-8 byte payload for a byte-level BPE token string."""
        if token in SPECIAL_TOKENS:
            return b""
        raw_bytes = bytearray()
        for char in token:
            if char in _U2B:
                raw_bytes.append(_U2B[char])
        return bytes(raw_bytes)

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        ids: list[int] = self._tok.encode(canonical_normalize(text), add_special_tokens=False).ids
        return self._frame_ids(ids, add_special_tokens)

    def _frame_ids(self, ids: list[int], add_special_tokens: bool) -> list[int]:
        """Apply the same structural framing as encode_with_offsets without spans."""
        if add_special_tokens:
            if not ids or ids[0] != self._bos_id:
                ids.insert(0, self._bos_id)
            if ids[-1] != self._eos_id:
                ids.append(self._eos_id)
        return ids

    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        clean_text = canonical_normalize(text)
        canonical_bytes = clean_text.encode("utf-8")
        num_bytes = len(canonical_bytes)

        encoding = self._tok.encode(clean_text, add_special_tokens=False)
        return self._offsets_from_encoding(encoding, num_bytes, add_special_tokens)

    def batch_encode_with_offsets(
        self,
        texts: Sequence[str],
        add_special_tokens: bool = False,
    ) -> list[tuple[list[int], list[tuple[int, int]]]]:
        clean = [canonical_normalize(text) for text in texts]
        encodings = self._tok.encode_batch(clean, add_special_tokens=False)
        return [
            self._offsets_from_encoding(encoding, len(text.encode("utf-8")), add_special_tokens)
            for text, encoding in zip(clean, encodings, strict=True)
        ]

    def _offsets_from_encoding(
        self,
        encoding: Encoding,
        num_bytes: int,
        add_special_tokens: bool,
    ) -> tuple[list[int], list[tuple[int, int]]]:
        content_ids = encoding.ids

        # Derive exact UTF-8 byte spans from token byte payloads
        content_offsets: list[tuple[int, int]] = []
        curr_offset = 0
        # AddedToken lstrip/rstrip can make Encoding.tokens include whitespace
        # absent from the vocabulary spelling. Protected fitted tokenizers have
        # no added tokens; other supplied backends keep the original ID lookup.
        token_strings = (
            [self.id_to_token(tid) for tid in content_ids]
            if self._tok.get_added_tokens_decoder()
            else encoding.tokens
        )
        for t_str in token_strings:
            byte_len = self._byte_lengths.get(t_str)
            if byte_len is None:
                byte_len = len(self.token_to_bytes(t_str))
                if len(self._byte_lengths) < 8192:
                    self._byte_lengths[t_str] = byte_len
            content_offsets.append((curr_offset, curr_offset + byte_len))
            curr_offset += byte_len

        if not add_special_tokens:
            return content_ids, content_offsets

        # Structural framing: add BOS and EOS, preventing duplicate framing
        final_ids: list[int] = []
        final_offsets: list[tuple[int, int]] = []

        has_bos = len(content_ids) > 0 and content_ids[0] == self._bos_id
        has_eos = len(content_ids) > 0 and content_ids[-1] == self._eos_id

        if not has_bos:
            final_ids.append(self._bos_id)
            final_offsets.append((0, 0))

        final_ids.extend(content_ids)
        final_offsets.extend(content_offsets)

        if not has_eos:
            final_ids.append(self._eos_id)
            final_offsets.append((num_bytes, num_bytes))

        return final_ids, final_offsets

    def decode(self, token_ids: Sequence[int], skip_special_tokens: bool = False) -> str:
        self.validate_token_ids(token_ids)
        filtered_ids: list[int] = []
        for tid in token_ids:
            if tid in self.special_token_ids:
                if skip_special_tokens:
                    continue
                # Special token IDs decode to literal bracketed strings
                filtered_ids.append(tid)
            else:
                filtered_ids.append(tid)

        return self._tok.decode(filtered_ids, skip_special_tokens=skip_special_tokens)

    def compute_byte_coverage(self, text: str) -> float:
        """Compute verified byte coverage ratio for text."""
        clean_text = canonical_normalize(text)
        canonical_bytes = clean_text.encode("utf-8")
        if not canonical_bytes:
            return 1.0

        content_ids, _ = self.encode_with_offsets(clean_text, add_special_tokens=False)
        covered_bytes = sum(len(self.token_to_bytes(self.id_to_token(tid))) for tid in content_ids)
        return float(covered_bytes) / float(len(canonical_bytes))

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # Save underlying tokenizer json
        tok_json_path = directory / "tokenizer.json"
        self._tok.save(str(tok_json_path))

        manifest = {
            "type": "bpe",
            "version": "1.0",
            "target_vocab_size": self._target_vocab_size,
            "actual_vocab_size": self.actual_vocab_size,
            "is_production_baseline": self._is_production_baseline,
            "special_tokens": {
                "pad": self._pad_id,
                "bos": self._bos_id,
                "eos": self._eos_id,
                "unk": self._unk_id,
            },
            "training_input_hash": self._training_input_hash,
            "fingerprint": self.fingerprint,
        }
        (directory / "tokenizer_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> ByteLevelBPETokenizer:
        manifest_path = directory / "tokenizer_manifest.json"
        tok_json_path = directory / "tokenizer.json"

        if not manifest_path.is_file() or not tok_json_path.is_file():
            raise FileNotFoundError(f"Missing tokenizer files in {directory}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tok = Tokenizer.from_file(str(tok_json_path))

        return cls(
            tokenizer=tok,
            target_vocab_size=manifest["target_vocab_size"],
            training_input_hash=manifest.get("training_input_hash"),
            is_production_baseline=manifest.get("is_production_baseline", False),
        )

    @classmethod
    def train_from_documents(
        cls,
        documents: Iterable[CanonicalDocument],
        target_vocab_size: int = 32768,
        max_train_docs: int = 100_000,
        max_train_bytes: int = 500 * 1024 * 1024,
        is_production_baseline: bool = False,
        c05_gate: MembershipGate | None = None,
        *,
        require_complete: bool = False,
        spool_dir: Path | None = None,
        on_feed: Callable[[int, int], None] | None = None,
    ) -> ByteLevelBPETokenizer:
        """Train a ByteLevel BPE tokenizer from canonical training documents.

        Strictly enforces that all supplied documents belong to the 'train' split.
        A frozen-sample caller passes the sample's exact document/byte totals as the
        caps with ``require_complete=True`` (see :func:`_fit_text_stream`).
        """
        _check_vocab_size(target_vocab_size)
        documents = screened_documents(documents, c05_gate, required=is_production_baseline)
        with _fit_text_stream(
            documents,
            max_train_docs,
            max_train_bytes,
            require_complete=require_complete,
            spool_dir=spool_dir,
            on_feed=on_feed,
        ) as (
            texts,
            training_input_hash,
        ):
            protected_tok = _train_protected(texts, target_vocab_size)
        return cls._fitted(
            protected_tok, target_vocab_size, training_input_hash, is_production_baseline
        )

    @classmethod
    def train_from_spool(
        cls,
        spool: Path,
        target_vocab_size: int,
        training_input_hash: str,
        *,
        documents: int,
        spool_bytes: int,
        is_production_baseline: bool = False,
        on_feed: Callable[[int, int], None] | None = None,
    ) -> ByteLevelBPETokenizer:
        """Train exactly like :meth:`train_from_documents` from an already screened spool.

        ``spool`` holds :func:`write_fit_frame` frames in feed order; the caller owns
        the C05 screening and the ``training_input_hash`` of those exact documents.
        Every frame must be consumed: a frame-count or byte mismatch refuses.
        """
        _check_vocab_size(target_vocab_size)
        fed = {"documents": 0, "bytes": 0}

        def counted(done: int, size: int) -> None:
            fed["documents"], fed["bytes"] = done, size
            if on_feed is not None:
                on_feed(done, size)

        with spool.open("rb") as stream:
            protected_tok = _train_protected(_read_frames(stream, counted), target_vocab_size)
            if stream.read(1):
                raise FitSampleBoundError("tokenizer-fit spool has unread frames")
        if (fed["documents"], fed["bytes"]) != (documents, spool_bytes):
            raise FitSampleBoundError("tokenizer-fit spool differs from the frozen sample")
        return cls._fitted(
            protected_tok, target_vocab_size, training_input_hash, is_production_baseline
        )

    @classmethod
    def _fitted(
        cls,
        protected_tok: Tokenizer,
        target_vocab_size: int,
        training_input_hash: str,
        is_production_baseline: bool,
    ) -> ByteLevelBPETokenizer:
        # Production baseline certification check
        # Tiny fixtures reaching vocabulary count alone cannot be certified as baseline.
        actual_prod = is_production_baseline and target_vocab_size == 32768
        return cls(
            tokenizer=protected_tok,
            target_vocab_size=target_vocab_size,
            training_input_hash=training_input_hash,
            is_production_baseline=actual_prod,
        )

    def count_valid_targets(self, texts: Sequence[str]) -> list[int]:
        """Exact C05 count rule via the native batch path, without offset construction.

        Same normalization, backend encode call and BOS/EOS framing as
        :meth:`encode_with_offsets`; only the byte spans are never built.
        """
        encodings = self._tok.encode_batch_fast(
            [canonical_normalize(text) for text in texts], add_special_tokens=False
        )
        counts: list[int] = []
        for encoding in encodings:
            ids = encoding.ids
            framed = len(ids) + 2
            if ids and ids[0] == self._bos_id:
                framed -= 1
            if ids and ids[-1] == self._eos_id:
                framed -= 1
            counts.append(max(0, framed - 1))
        return counts


def _check_vocab_size(target_vocab_size: int) -> None:
    if target_vocab_size < MINIMUM_VOCAB_SIZE:
        raise ValueError(
            f"Requested vocab size {target_vocab_size} is below required minimum "
            f"{MINIMUM_VOCAB_SIZE} (4 special tokens + 256 byte symbols)"
        )


def _train_protected(texts: Iterator[str], target_vocab_size: int) -> Tokenizer:
    """The single C06 BPE configuration: train, then strip added tokens."""
    # Initialize base tokenizer with BPE model
    base_tok = Tokenizer(BPE(unk_token="<unk>"))
    base_tok.pre_tokenizer = ByteLevel(add_prefix_space=False, use_regex=True)
    base_tok.decoder = ByteLevelDecoder()

    # Train with full ByteLevel alphabet and special tokens
    trainer = BpeTrainer(  # type: ignore[no-untyped-call]
        vocab_size=target_vocab_size,
        initial_alphabet=ByteLevel.alphabet(),
        special_tokens=SPECIAL_TOKENS,
        show_progress=False,
    )
    base_tok.train_from_iterator(texts, trainer=trainer)

    # Literal special-token protection:
    # Clear added_tokens from tokenizer structure so literal strings in user text
    # (such as '<eos>') are not intercepted before pre-tokenization.
    tok_dict = json.loads(base_tok.to_str())
    tok_dict["added_tokens"] = []
    protected_tok = Tokenizer.from_str(json.dumps(tok_dict))
    protected_tok.decoder = ByteLevelDecoder()
    return protected_tok


def write_fit_frame(stream: IO[bytes], doc: CanonicalDocument) -> int:
    """Append one length-framed canonical text exactly as the fit spool frames it."""
    raw = canonical_normalize(doc.text).encode("utf-8")
    stream.write(struct.pack("<Q", len(raw)))
    stream.write(raw)
    return len(raw)


def fit_input_line(doc: CanonicalDocument) -> bytes:
    """One document's contribution to ``training_input_hash`` (joined by newlines)."""
    return f"{doc.source_id}:{doc.doc_id}:{doc.clean_hash}".encode()


def _read_frames(
    stream: IO[bytes], on_feed: Callable[[int, int], None] | None = None
) -> Iterator[str]:
    fed = fed_bytes = 0
    while header := stream.read(8):
        if len(header) != 8:
            raise FitSampleBoundError("truncated tokenizer-fit spool frame")
        size = struct.unpack("<Q", header)[0]
        raw = stream.read(size)
        if len(raw) != size:
            raise FitSampleBoundError("truncated tokenizer-fit spool frame")
        fed += 1
        fed_bytes += size
        if on_feed is not None:
            on_feed(fed, fed_bytes)
        yield raw.decode("utf-8")
