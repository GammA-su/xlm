"""Final Mix-01 ``freeze`` / ``verify-freeze``, FAST: one streamed verification per shard.

Byte-identical to :func:`freeze.freeze` and :func:`freeze.verify_freeze` (kept as
``freeze-reference`` / ``verify-freeze-reference``, the oracles): the same signed
``freeze.json`` and ``training-data.json``. Only the work changes:

- C05 membership and the selection are streamed and authenticated once (as in
  :mod:`tokenfast`), never imported into SQLite.
- Each component shard is verified once, completely, with every reference check of
  :meth:`gates.MembershipGate.verify_token_shard` and
  :meth:`selection.SelectionGate.verify_component_shard`: file hashes (and the v2 byte
  table), the trusted C05 attestation and its binding, the manifest's component and
  tokenizer, and every index record against kept *train* membership and the selection
  (content, receipt, selection digest, counted/selected/valid targets, split, no
  repeated document), the exact component documents/targets and quota.
- The exposure plan is compiled by the unchanged :func:`freeze.exposure_plan` /
  ``compile_exposure_plan`` code, whose C05 shard check receives exactly those
  verified proofs (a path that was not verified refuses).

The index records are parsed with ``json.loads`` (the reference reader), in parallel.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.countfast import count_tables
from xlm.data.exclusion.fitfast import (
    Membership,
    StreamedC05,
    open_streamed,
    reconcile,
    stream_membership,
)
from xlm.data.exclusion.fitscan import OrderedPool, _cancel_check, init_worker
from xlm.data.exclusion.freeze import FREEZE_KIND, exposure_plan, final_recipe
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.selection import binding_of, check_binding, tokenizer_identity
from xlm.data.exclusion.supervisor import Deadline, Supervisor
from xlm.data.exclusion.tokenfast import (
    ComponentPlan,
    Selected,
    _Reporter,
    shard_binding,
    stream_selection,
    verify_selection_envelope,
    work_plan,
)
from xlm.data.tokens import TokenShardReader

if TYPE_CHECKING:
    from xlm.data.exclusion.gates import MembershipGate

LABEL = "FREEZE"
WORKER_CHOICES = (1, 2, 4, 8, 16)
INDEX_BLOCK_BYTES = 8 * MiB
#: An index line may carry v1 spans of a document of up to ``document_tokens`` tokens.
INDEX_LINE_CEILING = 512 * MiB
HASH_BLOCK = 8 * MiB


# -- worker side ----------------------------------------------------------------------


@dataclass(frozen=True)
class IndexTask:
    block: bytes
    receipt: str
    selection: str


@dataclass
class IndexChunk:
    rows: int
    ids: list[bytes]
    content: bytes  # 32 bytes per record (zero bytes where the field is not a digest)
    counted: npt.NDArray[np.int64]
    chosen: npt.NDArray[np.int64]
    valid: npt.NDArray[np.int64]
    lineage_ok: bool  # every record: c05_receipt, c05_selection and split as required


def _integer(value: Any) -> int:
    """An exact JSON integer, or -1 (never equal to a verified count)."""
    return value if type(value) is int else -1


def parse_index_block(task: IndexTask) -> IndexChunk:
    """``iter_document_offsets`` over complete lines: ``json.loads`` of every record."""
    ids: list[bytes] = []
    contents: list[bytes] = []
    counted: list[int] = []
    chosen: list[int] = []
    valid: list[int] = []
    ok = True
    zero = bytes(32)
    for raw in task.block.splitlines():
        if not raw.strip():
            continue
        if len(ids) % 4096 == 4095:
            _cancel_check()
        record = json.loads(raw)
        if type(record) is not dict:
            raise C05Error("token shard index record must be an object")
        doc_id, content = record.get("doc_id"), record.get("c05_content")
        if type(doc_id) is not str:
            raise C05Error("token shard document lacks exact kept membership")
        digest = zero
        if type(content) is str and len(content) == 64:
            try:
                digest = bytes.fromhex(content)
            except ValueError:
                digest = zero
            if digest.hex() != content:
                digest = zero
        ids.append(doc_id.encode("utf-8"))
        contents.append(digest)
        counted.append(_integer(record.get("c05_counted_valid_targets")))
        chosen.append(_integer(record.get("c05_selected_valid_targets")))
        valid.append(_integer(record.get("valid_targets")))
        ok = ok and (
            record.get("c05_receipt") == task.receipt
            and record.get("split") == "train"
            and record.get("c05_selection") == task.selection
        )
    return IndexChunk(
        len(ids),
        ids,
        b"".join(contents),
        np.asarray(counted, dtype=np.int64),
        np.asarray(chosen, dtype=np.int64),
        np.asarray(valid, dtype=np.int64),
        ok,
    )


def _index_blocks(path: Path, receipt: str, selection: str) -> Iterator[IndexTask]:
    pending = b""
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(INDEX_BLOCK_BYTES):
            data = pending + block if pending else block
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
                if len(pending) > INDEX_LINE_CEILING:
                    raise C05Error("token shard index record ceiling")
                continue
            pending = data[cut:]
            yield IndexTask(data[:cut], receipt, selection)
    if pending:
        yield IndexTask(pending, receipt, selection)


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


# -- parent ---------------------------------------------------------------------------


@dataclass
class VerifiedChain:
    view: StreamedC05
    m: Membership
    s: Selected
    plans: list[ComponentPlan]
    body: dict[str, Any]
    digest: str


def verified_chain(
    view: StreamedC05,
    selection_dir: Path,
    pool: OrderedPool,
    supervisor: Supervisor,
    progress: RunProgress | NullProgress,
) -> VerifiedChain:
    """Authenticated membership and selection (``SelectionGate`` equivalent)."""
    envelope = verify_selection_envelope(selection_dir, view)
    body: dict[str, Any] = envelope["payload"]
    membership, keys = count_tables(view)
    m = stream_membership(view, membership, pool, _Reporter(progress), supervisor, {})
    reconcile(view, m, keys)
    s = stream_selection(selection_dir, body, view, m, keys, pool, progress)
    plans = work_plan(view, m, s, "c07-offsets-v2", 2)
    return VerifiedChain(view, m, s, plans, body, str(envelope["digest"]))


def verify_component(
    directory: Path,
    plan: ComponentPlan,
    chain: VerifiedChain,
    pool: OrderedPool,
    hashes: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``verify_component_shard`` (incl. ``verify_token_shard``) of one shard, streamed.

    Returns the reference record and the ``{"manifest", "counters"}`` proof.
    """
    view, m, s = chain.view, chain.m, chain.s
    reader = TokenShardReader(directory)
    # verify_integrity: the v2 table is re-read and re-hashed here; the payload hashes
    # come from one streamed pass each (``hashes``).
    if reader.index_schema != "c07-offsets-v1":
        reader._byte_lengths = None
        reader.token_byte_table()
    for name, expected in (
        ("tokens.bin", reader.manifest.checksum_sha256),
        ("offsets.jsonl", reader.manifest.offsets_checksum_sha256),
    ):
        if hashes[name] != expected:
            raise ValueError(f"checksum mismatch for shard '{reader.manifest.shard_id}' {name}")
    size = (directory / "tokens.bin").stat().st_size
    if size != reader.manifest.num_tokens * reader.token_bytes_size:
        raise ValueError("Corrupted binary length")
    if view.mode != "protected" and view.mode != "authored":
        raise C05Error("authored membership cannot certify final token shards")
    manifest, counters = reader.manifest.to_dict(), reader.counters
    proof = verify_signed(
        read_metadata(directory / "c05-attestation.json", digested=False), view.trusted
    )
    if any(proof.get(k) != v for k, v in shard_binding(view, manifest, counters).items()):
        raise C05Error("token shard protected attestation mismatch")
    if reader.manifest.source_id != plan.name:
        raise C05Error("shard component differs from selection")
    if reader.manifest.tokenizer_hash != chain.body["tokenizer"]["fingerprint"]:
        raise C05Error("shard tokenizer differs from the exact-count tokenizer")
    rows = np.concatenate([f.selected for f in plan.files]) if plan.files else np.zeros(0, np.int64)
    offsets = m.id_offsets
    expected_rows = {
        m.ids[int(offsets[p]) : int(offsets[p + 1])]: int(n)
        for n, p in zip(rows.tolist(), s.position[rows].tolist(), strict=True)
    }
    seen = np.zeros(s.rows, dtype=np.bool_)
    documents = tokens = 0
    for chunk in pool.map(
        parse_index_block,
        _index_blocks(directory / "offsets.jsonl", view.receipt_digest, chain.digest),
    ):
        if not chunk.lineage_ok:
            raise C05Error("shard exact count or selection drifted")
        found = np.fromiter(
            (expected_rows.get(doc_id, -1) for doc_id in chunk.ids),
            dtype=np.int64,
            count=chunk.rows,
        )
        if np.any(found < 0):
            raise C05Error("shard document outside selected training membership")
        if np.any(seen[found]) or np.unique(found).size != found.size:
            raise C05Error("token shard repeats document membership")
        seen[found] = True
        content = np.frombuffer(chunk.content, dtype=np.uint8).reshape(chunk.rows, 32)
        if not np.array_equal(m.content[s.position[found]], content):
            raise C05Error("shard document outside selected training membership")
        if (
            not np.array_equal(chunk.counted, s.counted[found])
            or not np.array_equal(chunk.chosen, s.chosen[found])
            or not np.array_equal(chunk.valid, s.chosen[found])
        ):
            raise C05Error("shard exact count or selection drifted")
        documents += chunk.rows
        tokens += int(s.chosen[found].sum())
    if documents != reader.manifest.num_documents:
        raise C05Error("token shard membership count mismatch")
    if (documents, tokens) != (plan.documents, plan.valid_targets) or tokens != counters[
        "valid_targets"
    ]:
        raise C05Error("shard does not contain exactly the selected component membership")
    if tokens != chain.body["components"][plan.name]["quota"]:
        raise C05Error("shard valid targets differ from the frozen component quota")
    attestation = canonical.loads_bytes_strict((directory / "c05-attestation.json").read_bytes())
    record = {
        "source_id": plan.name,
        "shard_id": reader.manifest.shard_id,
        "manifest_digest": canonical.digest(manifest),
        "counters_digest": canonical.digest(counters),
        "attestation_digest": attestation["digest"],
        "tokens_sha256": reader.manifest.checksum_sha256,
        "offsets_sha256": reader.manifest.offsets_checksum_sha256,
        "documents": documents,
        "valid_targets": tokens,
    }
    return record, {"manifest": manifest, "counters": counters}


class VerifiedShardGate:
    """The C05 view ``compile_exposure_plan`` needs, answering with verified proofs only."""

    class _NoSeen:
        def execute(self, *_: Any) -> None:
            return None

    def __init__(self, view: StreamedC05, proofs: Mapping[str, dict[str, Any]]) -> None:
        self.mode = view.mode
        self.plan_digest = view.plan_digest
        self.receipt_digest = view.receipt_digest
        self.db = self._NoSeen()
        self._proofs = {str(Path(p).resolve()): proof for p, proof in proofs.items()}

    def verify_token_shard(
        self, directory: Path, *, reset_seen: bool = True, rehearsal: bool = False
    ) -> dict[str, Any]:
        if self.mode != "protected" and not (rehearsal and self.mode == "authored"):
            raise C05Error("authored membership cannot certify final token shards")
        proof = self._proofs.get(str(Path(directory).resolve()))
        if proof is None:
            raise C05Error("token shard was not verified by this freeze")
        return proof


def verify_shards(
    chain: VerifiedChain,
    shards: Mapping[str, Path],
    pool: OrderedPool,
    progress: RunProgress | NullProgress,
    hash_threads: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Every component shard verified once; payload hashes taken by parallel threads."""
    by_name = {p.name: p for p in chain.plans}
    total = sum(
        (shards[c] / n).stat().st_size for c in shards for n in ("tokens.bin", "offsets.jsonl")
    )
    progress.stage("SHARD HASH", total, "bytes")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, hash_threads)) as threads:
        futures = {
            (c, n): threads.submit(_file_sha, shards[c] / n)
            for c in sorted(shards)
            for n in ("tokens.bin", "offsets.jsonl")
        }
        done = 0
        hashes: dict[str, dict[str, str]] = {c: {} for c in shards}
        for (component, name), future in futures.items():
            hashes[component][name] = future.result()
            done += (shards[component] / name).stat().st_size
            elapsed = max(time.monotonic() - started, 1e-9)
            progress.update(done, mib_per_s=done / MiB / elapsed)
    progress.stage("SHARD VERIFY", len(shards), "shards")
    records: dict[str, dict[str, Any]] = {}
    proofs: dict[str, dict[str, Any]] = {}
    for n, component in enumerate(sorted(shards)):
        record, proof = verify_component(
            shards[component], by_name[component], chain, pool, hashes[component]
        )
        records[component] = {**record, "path": str(shards[component])}
        proofs[str(shards[component])] = proof
        progress.update(n + 1)
    return records, proofs


def _supervised(
    view: StreamedC05, workers: int, inline: bool, started: float
) -> tuple[Supervisor, OrderedPool]:
    membership, _ = count_tables(view)
    supervisor = Supervisor(Deadline(None, started), view.plan.resources.ram_bytes)
    pool = OrderedPool(workers, membership, supervisor, inline=inline, initializer=init_worker)
    return supervisor, pool


def freeze_fast(
    proof: Path,
    selection_dir: Path,
    shards_root: Path,
    tokenizer_dir: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    block_size: int = 8192,
    workers: int = 8,
    progress: RunProgress | NullProgress | None = None,
    inline: bool = False,
    allow_authored: bool = True,
) -> dict[str, Any]:
    """:func:`freeze.freeze`, streamed (see module doc); returns the signed envelope."""
    if workers not in WORKER_CHOICES:
        raise C05Error("freeze workers must be 1, 2, 4, 8 or 16")
    progress = progress or NullProgress()
    started = time.monotonic()
    progress.stage("PROOF VERIFY", None, "steps")
    view = open_streamed(
        proof,
        allow_authored=allow_authored,
        consumes=[selection_dir, shards_root, tokenizer_dir, output],
    )
    if view.trusted.get(issuer) != key:
        raise C05Error("allocation signer is not trusted")
    progress.stage("TOKENIZER VERIFY", None, "steps")
    _, identity = tokenizer_identity(tokenizer_dir, view)
    supervisor, pool = _supervised(view, workers, inline, started)
    with supervisor, pool:
        chain = verified_chain(view, selection_dir, pool, supervisor, progress)
        if identity != chain.body["tokenizer"]:
            raise C05Error("freeze tokenizer differs from the exact-count tokenizer")
        components: dict[str, dict[str, int]] = chain.body["components"]
        present = sorted(p.name for p in shards_root.iterdir() if not p.name.startswith("."))
        if present != sorted(components):
            raise C05Error("freeze shard set differs from the selected components")
        shards = {c: (shards_root / c).resolve() for c in sorted(components)}
        records, proofs = verify_shards(chain, shards, pool, progress, workers)
    progress.stage("EXPOSURE PLAN", None, "steps")
    total = int(chain.body["valid_target_quota"])
    recipe = final_recipe(view.mode, components, total)
    exposure = exposure_plan(recipe, _as_gate(view, proofs), records, components, total, block_size)
    envelope = signed(
        {
            "kind": FREEZE_KIND,
            **binding_of(view),
            "selection_path": str(selection_dir.resolve()),
            "selection_digest": chain.digest,
            "selected_membership_sha256": chain.body["selected_membership_sha256"],
            "counts_digest": chain.body["counts_digest"],
            "tokenizer": identity,
            "quota_sha256": chain.body["quota_sha256"],
            "requirements_digest": chain.body["requirements_digest"],
            "recipe": recipe.model_dump(mode="json"),
            "recipe_identity": recipe.identity(),
            "exposure_plan": exposure,
            "exposure_plan_digest": canonical.digest(exposure),
            "block_size": block_size,
            "shards": records,
            "valid_targets": total,
        },
        issuer,
        key,
    )
    progress.stage("PUBLISH", None, "steps")
    output.mkdir(parents=True, exist_ok=True)
    write_once(output / "freeze.json", envelope)
    write_once(
        output / "training-data.json",
        {
            "mixture": recipe.model_dump(mode="json"),
            "sources": {c: r["path"] for c, r in records.items()},
            "exposure_plan": exposure,
            "c05_proof": str(proof.resolve()),
            "c05_freeze": str((output / "freeze.json").resolve()),
        },
    )
    progress.complete()
    return envelope


def verify_freeze_fast(
    path: Path,
    proof: Path,
    *,
    workers: int = 8,
    progress: RunProgress | NullProgress | None = None,
    inline: bool = False,
    allow_authored: bool = True,
) -> dict[str, Any]:
    """:func:`freeze.verify_freeze`, streamed: signature, selection, every shard's bytes."""
    from xlm.data.sampling import MixtureRecipe

    if workers not in WORKER_CHOICES:
        raise C05Error("freeze workers must be 1, 2, 4, 8 or 16")
    progress = progress or NullProgress()
    started = time.monotonic()
    progress.stage("PROOF VERIFY", None, "steps")
    envelope = read_metadata(path, digested=False)
    preview = envelope.get("payload", {}) if isinstance(envelope, dict) else {}
    shard_paths = [r.get("path", "") for r in dict(preview.get("shards", {})).values()]
    view = open_streamed(
        proof,
        allow_authored=allow_authored,
        consumes=[path, str(preview.get("selection_path", "")), *shard_paths],
    )
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, FREEZE_KIND)
    supervisor, pool = _supervised(view, workers, inline, started)
    with supervisor, pool:
        chain = verified_chain(view, Path(body["selection_path"]), pool, supervisor, progress)
        if (
            chain.digest != body["selection_digest"]
            or chain.body["tokenizer"] != body["tokenizer"]
            or chain.body["selected_membership_sha256"] != body["selected_membership_sha256"]
            or chain.body["counts_digest"] != body["counts_digest"]
        ):
            raise C05Error("freeze selection changed")
        components = chain.body["components"]
        if set(body["shards"]) != set(components):
            raise C05Error("freeze shard set differs from the selection")
        shards = {c: Path(body["shards"][c]["path"]) for c in sorted(components)}
        records, proofs = verify_shards(chain, shards, pool, progress, workers)
    for component in sorted(components):
        if {**records[component], "path": body["shards"][component]["path"]} != body["shards"][
            component
        ]:
            raise C05Error("frozen shard bytes changed")
    recipe = MixtureRecipe.model_validate(body["recipe"])
    total = int(chain.body["valid_target_quota"])
    if recipe.identity() != body["recipe_identity"] or recipe.model_dump(
        mode="json"
    ) != final_recipe(view.mode, components, total).model_dump(mode="json"):
        raise C05Error("freeze recipe differs from frozen quota shares")
    progress.stage("EXPOSURE PLAN", None, "steps")
    exposure = exposure_plan(
        recipe,
        _as_gate(view, proofs),
        body["shards"],
        components,
        total,
        body["block_size"],
    )
    if exposure != body["exposure_plan"]:
        raise C05Error("freeze exposure plan differs from the frozen shards")
    progress.complete()
    return envelope


def _as_gate(view: StreamedC05, proofs: Mapping[str, dict[str, Any]]) -> MembershipGate:
    """The verified-proof view, typed as the gate ``exposure_plan`` consumes (duck-typed:
    ``mode``, digests, ``db.execute`` and ``verify_token_shard`` only)."""
    return cast("MembershipGate", VerifiedShardGate(view, proofs))
