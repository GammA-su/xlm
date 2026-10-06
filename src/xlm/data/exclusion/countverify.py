"""Independent verification of a published exact-count artifact (``verify-counts``).

Read-only and content-free. Checks, in order:

1. the C05 proof (streamed view: plan, completion, membership seal, trust root);
2. the tokenizer identity the reference records, and an optional fingerprint pin;
3. ``counts.json``: signature by a trusted issuer, kind, C05 binding, counting rule and
   tokenizer identity; the directory holds exactly ``counts.json`` and ``counts.jsonl``;
4. with ``--c06-fit``: the fit and kept index verify (:func:`countbind.open_c06`) and
   the signed ``c06_fit`` names exactly them;
5. ``counts.jsonl`` in one pass: size and SHA-256 equal the signed values; every row is
   canonical JSON with exactly the four count fields, a frozen-plan allocation, a
   non-negative integer count and a strictly ascending doc id; with the C06 fit every
   row equals the kept index's next train row (doc id, content, allocation);
6. per-allocation documents and valid targets recomputed from the rows equal the
   signed totals; the document count equals the signed count, the kept index and the
   optional pin; the fit is unchanged on disk afterwards.

The returned summary carries only digests, counts and allocation keys.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import verify_signed
from xlm.data.exclusion.countbind import CountPins, check_totals, open_c06
from xlm.data.exclusion.fitfast import open_streamed
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.selection import (
    COUNT_RULE,
    allocation_key,
    check_binding,
    tokenizer_identity,
)

COUNT_KIND = "c05_exact_token_counts_v1"
ROW_KEYS = frozenset({"allocation", "content", "doc_id", "valid_targets"})
NAMES = ["counts.json", "counts.jsonl"]
BLOCK = 8 * 1024**2
HEX = frozenset("0123456789abcdef")


def verify_counts_artifact(
    proof: Path,
    tokenizer_dir: Path,
    counts_dir: Path,
    *,
    c06: Path | None = None,
    pins: CountPins | None = None,
    progress: RunProgress | NullProgress | None = None,
) -> dict[str, Any]:
    pins = pins or CountPins()
    if c06 is None and pins.needs_fit():
        raise C05Error("C06 fit or kept-index pins require the C06 fit directory")
    progress = progress or NullProgress()
    progress.stage("PROOF VERIFY", None, "steps")
    extra: list[Path | str] = [] if c06 is None else [c06]
    view = open_streamed(proof, allow_authored=True, consumes=[tokenizer_dir, counts_dir, *extra])
    progress.stage("TOKENIZER VERIFY", None, "steps")
    _, identity = tokenizer_identity(tokenizer_dir, view)
    pins.check_tokenizer(identity)
    progress.stage("ENVELOPE VERIFY", None, "steps")
    if sorted(p.name for p in counts_dir.iterdir()) != NAMES:
        raise C05Error("count artifact directory holds unaccounted files")
    envelope = read_metadata(counts_dir / "counts.json", digested=False)
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, COUNT_KIND)
    if body.get("rule") != COUNT_RULE or body.get("tokenizer") != dict(identity):
        raise C05Error("exact counts use a different tokenizer or counting rule")
    binding = None
    expected_rows: tuple[Any, ...] | None = None
    if c06 is not None:
        progress.stage("C06 BINDING VERIFY", None, "steps")
        binding = open_c06(c06, view, identity, pins)
        if body.get("c06_fit") != binding.record():
            raise C05Error("exact counts are not bound to this C06 fit")
        index = binding.index
        assert index is not None
        train = np.flatnonzero(index.rows["assigned_split"] == 0)
        names = [allocation_key(*a) for a in index.allocations]
        expected_rows = (
            index.id_offsets[train].tolist(),
            index.id_offsets[train + 1].tolist(),
            bytes(index.ids),
            index.rows["content"][train],
            [names[a] for a in index.rows["allocation"][train].tolist()],
        )
        binding.release()
    plan_allocations = {
        allocation_key(i.component, i.view, i.upstream_component) for i in view.plan.files
    }
    signed_bytes, signed_sha = body.get("counts_bytes"), body.get("counts_sha256")
    path = counts_dir / "counts.jsonl"
    if type(signed_bytes) is not int or path.stat().st_size != signed_bytes:
        raise C05Error("exact count artifact changed")
    progress.stage("COUNTS VERIFY", body.get("documents"), "rows")
    digest = hashlib.sha256()
    totals: dict[str, dict[str, int]] = {}
    previous = b""
    rows = done = 0
    rest = b""
    with path.open("rb") as stream:
        while True:
            block = stream.read(BLOCK)
            digest.update(block)
            done += len(block)
            data = rest + block
            lines = data.split(b"\n")
            rest = lines.pop()
            for line in lines:
                doc_id, content, key, tokens = _row(line + b"\n", plan_allocations)
                raw_id = doc_id.encode("utf-8")
                if rows and raw_id <= previous:
                    raise C05Error("count rows are not strictly ascending by doc id")
                if expected_rows is not None:
                    starts, ends, ids, contents, allocations = expected_rows
                    if rows >= len(starts) or (
                        ids[starts[rows] : ends[rows]] != raw_id
                        or contents[rows].tobytes().hex() != content
                        or allocations[rows] != key
                    ):
                        raise C05Error("count row differs from the C06 kept index")
                previous = raw_id
                total = totals.setdefault(key, {"documents": 0, "valid_targets": 0})
                total["documents"] += 1
                total["valid_targets"] += tokens
                rows += 1
                if rows % 65536 == 0:
                    progress.update(rows, bytes_done=done, bytes_total=signed_bytes)
            if not block:
                break
    if rest:
        raise C05Error("count artifact does not end with a complete row")
    progress.update(rows, bytes_done=done, bytes_total=signed_bytes)
    if done != signed_bytes or digest.hexdigest() != signed_sha:
        raise C05Error("exact count artifact changed")
    if expected_rows is not None and rows != len(expected_rows[0]):
        raise C05Error("count rows differ from the C06 kept index train rows")
    allocations = dict(sorted(totals.items()))
    if allocations != body.get("allocations") or rows != body.get("documents"):
        raise C05Error("signed count totals differ from the count rows")
    pins.check_documents(rows)
    if binding is not None:
        check_totals(binding, allocations)
        binding.reverify()
    progress.complete()
    return {
        "verified": True,
        "mode": body["mode"],
        "counts_digest": envelope["digest"],
        "counts_sha256": signed_sha,
        "counts_bytes": signed_bytes,
        "documents": rows,
        "valid_targets": sum(t["valid_targets"] for t in allocations.values()),
        "allocations": allocations,
        "plan_digest": body["plan_digest"],
        "completion_digest": body["completion_digest"],
        "tokenizer_fingerprint": identity["fingerprint"],
        "tokenizer_files_digest": identity["files_digest"],
        "c06_fit": body.get("c06_fit"),
        "kept_index_rows_compared": expected_rows is not None,
    }


def _row(line: bytes, plan_allocations: set[str]) -> tuple[str, str, str, int]:
    try:
        row = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise C05Error("count row is not strict canonical JSON") from exc
    if type(row) is not dict or row.keys() != ROW_KEYS:
        raise C05Error("count row schema")
    if canonical.canonical_bytes(row) + b"\n" != line:
        raise C05Error("count row is not canonical")
    doc_id, content, allocation, tokens = (
        row["doc_id"],
        row["content"],
        row["allocation"],
        row["valid_targets"],
    )
    if type(doc_id) is not str or type(tokens) is not int or tokens < 0:
        raise C05Error("count row value")
    if type(content) is not str or len(content) != 64 or not set(content) <= HEX:
        raise C05Error("count row content digest")
    if (
        type(allocation) is not list
        or len(allocation) != 3
        or (key := allocation_key(*allocation)) not in plan_allocations
    ):
        raise C05Error("count row allocation is not a frozen plan allocation")
    return doc_id, content, key, tokens
