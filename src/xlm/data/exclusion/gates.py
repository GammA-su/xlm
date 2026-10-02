"""Verified kept-membership gates shared by tokenizer and tokenization paths."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xlm.tokenizers.base import BaseTokenizer

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed, verify_signed
from xlm.data.exclusion.inputs import COMPONENTS, read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.runner import verify_completion

# Actual canonical source identities, not acquisition directory aliases.
BASELINE_SOURCES = frozenset(
    {
        "essential_web",
        "ultrax_ultrafineweb",
        "finepdfs_edu",
        "synth",
        "nemotron_specialized",
        "finewiki",
        "ifm_behaviors",
        "common_pile",
        "simple_stories",
    }
)
PRODUCTION_COMPONENTS = BASELINE_SOURCES | COMPONENTS


class MembershipGate:
    """Import a verified content-free ledger into a private bounded lookup index.

    The caller supplies the current sealed manifest, not merely a receipt ID. The
    full canonical record must match; renamed/regenerated/top-up records need new
    screening. Scratch must be a fresh file under the downstream job's own budget.
    """

    def __init__(
        self,
        directory: Path,
        plan: ExecutionPlan,
        manifest: Mapping[str, Any],
        trusted: Mapping[str, bytes],
        scratch: Path,
        *,
        authored: bool = False,
        signer: tuple[str, bytes] | None = None,
    ) -> None:
        if plan.mode != ("authored" if authored else "protected"):
            raise C05Error("development evidence cannot satisfy protected C05")
        if (
            manifest.get("digest") != plan.input_manifest_digest
            or canonical.self_digest(manifest) != plan.input_manifest_digest
        ):
            raise C05Error("current corpus manifest differs from C05")
        completion = verify_completion(directory, plan, trusted)
        self.plan = plan
        self.directory = directory
        self.receipt_digest = str(completion["digest"])
        self.plan_digest = plan.identity()
        self.mode = plan.mode
        self.completion = completion["payload"]
        self.input_manifest = dict(manifest)
        self.trusted = dict(trusted)
        self.signer = signer
        if signer is not None and self.trusted.get(signer[0]) != signer[1]:
            raise C05Error("downstream signer is not a trusted issuer")
        if scratch.exists():
            raise C05Error("membership lookup must use a fresh private scratch file")
        scratch.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(scratch)
        self.db.execute("PRAGMA cache_size=-8192")
        self.db.execute(f"PRAGMA max_page_count={max(1, plan.resources.index_bytes // 4096)}")
        self.db.execute(
            "CREATE TABLE membership(id TEXT PRIMARY KEY,content TEXT,decision TEXT,split TEXT,"
            "component TEXT,view TEXT,upstream TEXT)"
        )
        self.db.execute("CREATE TABLE seen(id TEXT PRIMARY KEY)")
        self.db.execute("CREATE INDEX allocation ON membership(component,view,upstream,split)")
        count = 0
        try:
            with self.db, (directory / "membership.jsonl").open("rb") as stream:
                while raw := stream.readline(plan.resources.document_bytes + 1):
                    if len(raw) > plan.resources.document_bytes:
                        raise C05Error("membership record ceiling")
                    row = canonical.loads_bytes_strict(raw)
                    self.db.execute(
                        "INSERT INTO membership VALUES(?,?,?,?,?,?,?)",
                        (
                            row["doc_id"],
                            row["content"],
                            row["decision"],
                            row["split"],
                            row["component"],
                            row["view"],
                            row["upstream_component"],
                        ),
                    )
                    count += 1
                    if count > plan.resources.records:
                        raise C05Error("membership record count ceiling")
            if count != completion["payload"]["kept"]:
                raise C05Error("membership count disagrees with completion")
            # Refuse a replacement during import; no unverified lookup escapes.
            verify_completion(directory, plan, trusted)
        except BaseException:
            self.db.close()
            raise

    def verify(self, doc: CanonicalDocument) -> None:
        row = self.db.execute(
            "SELECT content,decision,split FROM membership WHERE id=?", (doc.doc_id,)
        ).fetchone()
        if (
            row is None
            or row != (canonical.digest(doc.to_dict()), "kept", "train")
            or doc.split != "train"
        ):
            raise C05Error("document is not exact screened training membership")

    def lookup(self, doc_id: str) -> tuple[str, str, str, str, str, str | None] | None:
        """Content digest, decision, split and allocation of one kept record."""
        row = self.db.execute(
            "SELECT content,decision,split,component,view,upstream FROM membership WHERE id=?",
            (doc_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def verify_token_shard(
        self, directory: Path, *, reset_seen: bool = True, rehearsal: bool = False
    ) -> dict[str, Any]:
        """Verify shard bytes plus every document against current kept membership.

        Token offsets bind original record content, not a receipt-ID assertion.
        The tokenizer itself is separately frozen by the existing shard manifest.
        ``rehearsal`` lets an authored gate verify authored shards for an explicitly
        authored chain; the attestation binds the mode, so neither can stand in for
        the other, and production consumers never pass it.
        """
        from xlm.data.tokens import TokenShardReader

        if self.mode != "protected" and not (rehearsal and self.mode == "authored"):
            raise C05Error("authored membership cannot certify final token shards")
        reader = TokenShardReader(directory)
        reader.verify_integrity()
        proof = verify_signed(
            read_metadata(directory / "c05-attestation.json", digested=False), self.trusted
        )
        if any(
            proof.get(k) != v
            for k, v in self._shard_binding(reader.manifest.to_dict(), reader.counters).items()
        ):
            raise C05Error("token shard protected attestation mismatch")
        count = 0
        if reset_seen:
            self.db.execute("DELETE FROM seen")
        for offset in reader.iter_document_offsets():
            row = self.db.execute(
                "SELECT content,decision,split FROM membership WHERE id=?", (offset["doc_id"],)
            ).fetchone()
            if row is None or row != (offset.get("c05_content"), "kept", "train"):
                raise C05Error("token shard document lacks exact kept membership")
            if offset.get("c05_receipt") != self.receipt_digest or offset.get("split") != "train":
                raise C05Error("token shard C05 lineage changed")
            try:
                self.db.execute("INSERT INTO seen VALUES(?)", (offset["doc_id"],))
            except sqlite3.IntegrityError as exc:
                raise C05Error("token shard repeats document membership") from exc
            count += 1
        if count != reader.manifest.num_documents:
            raise C05Error("token shard membership count mismatch")
        return {"manifest": reader.manifest.to_dict(), "counters": reader.counters}

    def _shard_binding(self, manifest: dict[str, Any], counters: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "c05_token_shard_v2",
            "mode": self.mode,
            "plan_digest": self.plan_digest,
            "receipt_digest": self.receipt_digest,
            "shard_manifest_digest": canonical.digest(manifest),
            "counters_digest": canonical.digest(counters),
        }

    def seal_token_shard(
        self, manifest: dict[str, Any], counters: dict[str, Any]
    ) -> dict[str, Any]:
        if self.signer is None:
            raise C05Error(
                "screened token publication requires an explicit trusted signing identity"
            )
        return signed(self._shard_binding(manifest, counters), *self.signer)

    def documents(self, docs: Iterable[CanonicalDocument]) -> Iterator[CanonicalDocument]:
        self.db.execute("DELETE FROM seen")
        for doc in docs:
            self.verify(doc)
            try:
                self.db.execute("INSERT INTO seen VALUES(?)", (doc.doc_id,))
            except sqlite3.IntegrityError as exc:
                raise C05Error("duplicate document in downstream membership") from exc
            yield doc

    def close(self) -> None:
        self.db.close()


def screened_documents(
    documents: Iterable[CanonicalDocument], gate: MembershipGate | None, *, required: bool = False
) -> Iterator[CanonicalDocument]:
    if required and gate is None:
        raise C05Error("production input requires verified protected C05 membership")
    if required and gate is not None and gate.mode != "protected":
        raise C05Error("authored membership cannot certify production input")
    if gate is not None:
        yield from gate.documents(documents)
        return
    for doc in documents:
        if doc.source_id in BASELINE_SOURCES:
            raise C05Error("baseline first-pass input requires verified C05 membership")
        yield doc


def count_exact_tokens(
    documents: Iterable[CanonicalDocument], tokenizer: BaseTokenizer, gate: MembershipGate
) -> dict[str, int | str]:
    """Exact no-special-token count over unique, protected screened training rows.

    This deliberately does not infer final component sufficiency from a /4 byte
    estimate, alter quotas, or authorize tokenizer fitting or gradient training.
    """
    tokens = records = 0
    for doc in screened_documents(documents, gate, required=True):
        records += 1
        tokens += len(tokenizer.encode(doc.text, add_special_tokens=False))
    return {
        "documents": records,
        "tokens": tokens,
        "tokenizer": tokenizer.fingerprint,
        "receipt_digest": gate.receipt_digest,
    }
