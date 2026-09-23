"""Exact bounded micro-experiments; candidate algorithms do not change the cleaner."""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import re
import time
import tracemalloc
from collections.abc import Iterator, Sequence
from pathlib import Path

from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.cleaning.features import CharStats, compute_char_stats
from xlm.data.cleaning.language import _WORD_RE, ENGLISH_STOP_WORDS
from xlm.data.cleaning.pii import _NON_EMAIL_HINT_RE, SECRET_PATTERNS, is_reserved_example_email

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def chars(text: str, mode: str) -> tuple[int, ...]:
    if mode == "reference" or text.isascii():
        stats = compute_char_stats(text)
    else:
        ascii_text = text.encode("ascii", "ignore").decode("ascii")
        stats = compute_char_stats(ascii_text)
        for match in _NON_ASCII.finditer(text):
            char = match.group()
            if char.isalpha():
                stats.alpha += 1
            if char.isalnum():
                stats.alnum += 1
            if not char.isspace():
                stats.non_ws += 1
                if char.isdigit():
                    stats.digits += 1
                elif not char.isalnum():
                    stats.symbols += 1
    return tuple(getattr(stats, k) for k in CharStats.__slots__)


def language(text: str, mode: str) -> tuple[int, int]:
    if mode == "reference" or not text.isascii():
        words = [w.lower() for w in _WORD_RE.findall(text.strip())]
    else:
        words = _WORD_RE.findall(text.strip().lower())
    if mode == "counter":
        counts = collections.Counter(words)
        return len(words), sum(counts[w] for w in ENGLISH_STOP_WORDS)
    return len(words), sum(w in ENGLISH_STOP_WORDS for w in words)


def repetition(text: str, mode: str) -> tuple[int, int]:
    words = text[:50000].split()
    sequence: Sequence[str | int]
    if mode == "ordinal":
        ids: dict[str, int] = {}
        sequence = [ids.setdefault(word, len(ids)) for word in words]
    else:
        sequence = words
    grams: Iterator[tuple[str | int, ...]]
    if mode == "reference":
        grams = (tuple(sequence[i : i + 5]) for i in range(len(sequence) - 4))
    else:
        grams = zip(*(itertools.islice(sequence, i, None) for i in range(5)), strict=False)
    counts = collections.Counter(grams)
    return sum(v for v in counts.values() if v > 1), sum(counts.values())


def structure(text: str, mode: str) -> bool:
    if mode != "reference" and text.strip()[:1] not in "-*_=|:+":
        return False
    stripped = text.strip()
    if len(stripped) < 3:
        return False
    compact = stripped.replace(" ", "").replace("\t", "")
    if len(compact) < 3:
        return False
    chars = set(compact)
    return any(chars <= {c} for c in "-*_=") or (
        "|" in compact and chars <= set("|-:+=") and ("-" in compact or "=" in compact)
    )


def pii(text: str, mode: str) -> tuple[list[str], int]:
    found: list[str] = []
    reserved = 0
    hint = _NON_EMAIL_HINT_RE.search(text) is not None if mode == "reference" else False
    lower = text.lower() if mode != "reference" else ""
    sentinels = {
        "huggingface_token": "hf_",
        "aws_access_key": "AKIA",
        "github_token": "ghp_",
        "private_key": "-----BEGIN ",
        "canary_credential": "CANARY_SECRET_",
        "ssn": "-",
    }
    for kind, pattern in SECRET_PATTERNS:
        if kind == "email_address":
            real = False
            if mode == "reference" or "@" in text:
                for match in pattern.finditer(text):
                    if is_reserved_example_email(match.group()):
                        reserved += 1
                    else:
                        real = True
            if real:
                found.append(kind)
        else:
            gate = (
                hint
                if mode == "reference"
                else ("bearer" in lower if kind == "bearer_token" else sentinels[kind] in text)
            )
            if gate and pattern.search(text):
                found.append(kind)
    return found, reserved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chars-only", action="store_true")
    args = parser.parse_args()
    texts = [
        d.text for d in itertools.islice(CanonicalDatasetReader.read_jsonl(args.source), 10000)
    ]
    rows = []
    experiments = (
        (
            ("language", language, ["reference", "lower", "counter"]),
            ("repetition", repetition, ["reference", "zip", "ordinal"]),
            ("pii", pii, ["reference", "sentinels"]),
            ("structure", structure, ["reference", "prefix"]),
        )
        if not args.chars_only
        else (("chars", chars, ["reference", "mixed"]),)
    )
    for name, function, modes in experiments:
        expected = [function(text, "reference") for text in texts]
        for repeat in range(2):
            for mode in modes if repeat == 0 else reversed(modes):
                started = time.perf_counter()
                actual = [function(text, mode) for text in texts]
                elapsed = time.perf_counter() - started
                assert actual == expected, (name, mode)
                rows.append({"function": name, "mode": mode, "repeat": repeat, "seconds": elapsed})
    allocations = []
    for name, function, allocation_modes in (
        ("language", language, ("reference", "lower")),
        ("repetition", repetition, ("reference", "zip", "ordinal")),
    ):
        for mode in allocation_modes:
            tracemalloc.start()
            for text in texts[:100]:
                function(text, mode)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            allocations.append({"function": name, "mode": mode, "python_peak_bytes": peak})
    args.output.write_text(
        json.dumps({"rows": rows, "allocations": allocations}, indent=2), encoding="utf-8"
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
