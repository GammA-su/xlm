"""Bounded protected token-overlap candidates. Never an automatic exclusion signal."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import C05Error, Resources, ReviewPolicy
from xlm.data.exclusion.storage import OrderedConnection
from xlm.data.exclusion.streaming import Pattern


def queue_digest(db: OrderedConnection) -> str:
    value = hashlib.sha256()
    for row in db.execute("SELECT * FROM review_queue ORDER BY doc,ref,pattern"):
        db.check()
        value.update(canonical.canonical_bytes(row) + b"\n")
    return value.hexdigest()


class ReviewQueue:
    def __init__(self, db: OrderedConnection, policy: ReviewPolicy, resources: Resources) -> None:
        self.db, self.policy, self.resources = db, policy, resources
        db.executescript("""
            CREATE TABLE IF NOT EXISTS review_patterns(id TEXT PRIMARY KEY,tokens TEXT);
            CREATE TABLE IF NOT EXISTS review_refs(id TEXT,ref TEXT,PRIMARY KEY(id,ref));
            CREATE TABLE IF NOT EXISTS review_tokens(token TEXT,id TEXT,PRIMARY KEY(token,id));
            CREATE TABLE IF NOT EXISTS review_queue(
                doc TEXT,ref TEXT,pattern TEXT,matched INTEGER,total INTEGER,
                PRIMARY KEY(doc,ref,pattern));
            CREATE INDEX IF NOT EXISTS review_ref ON review_queue(ref,doc,pattern);
        """)

    def compile(self, entries: Iterable[Pattern], check: Callable[[], None]) -> None:
        """Rebuild the derived immutable index, preserving the transactional queue."""
        with self.db:
            for table in ("review_tokens", "review_refs", "review_patterns"):
                self.db.execute(f"DELETE FROM {table}")
            for count, entry in enumerate(entries, 1):
                check()
                if count > self.resources.benchmark_patterns:
                    raise C05Error("review pattern ceiling")
                unique = sorted(set(entry.tokens))
                if (
                    len(entry.tokens) < self.policy.min_tokens
                    or len(unique) < self.policy.min_distinct
                ):
                    continue
                identity = canonical.digest(entry.tokens)
                self.db.execute(
                    "INSERT OR IGNORE INTO review_patterns VALUES(?,?)",
                    (identity, canonical.canonical_bytes(unique).decode()),
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO review_tokens VALUES(?,?)",
                    ((t, identity) for t in unique),
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO review_refs VALUES(?,?)",
                    # All rendered variants of one item share its review ceiling.
                    ((identity, ref.rsplit(":", 1)[0]) for ref in entry.provenance),
                )

    def consider(self, doc: str, tokens: list[str], spend: Callable[[], None]) -> None:
        policy = self.policy
        unique = set(tokens)
        if len(tokens) < policy.min_tokens or len(unique) < policy.min_distinct:
            return
        candidates: set[str] = set()
        for token in sorted(unique)[: policy.query_tokens]:
            rows = self.db.execute(
                "SELECT id FROM review_tokens WHERE token=? ORDER BY id LIMIT ?",
                (token, policy.postings_per_token),
            )
            for (identity,) in rows:
                candidates.add(identity)
                if len(candidates) >= policy.comparisons_per_document:
                    break
            if len(candidates) >= policy.comparisons_per_document:
                break
        emitted = 0
        for identity in sorted(candidates):
            spend()
            row = self.db.execute(
                "SELECT tokens FROM review_patterns WHERE id=?", (identity,)
            ).fetchone()
            pattern = set(canonical.loads_strict(row[0]))
            matched = len(pattern & unique)
            if matched / len(pattern) < policy.matched_fraction:
                continue
            for (ref,) in self.db.execute(
                "SELECT ref FROM review_refs WHERE id=? ORDER BY ref", (identity,)
            ):
                if emitted >= policy.candidates_per_document:
                    return
                total = self.db.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]
                if total >= self.resources.review_candidates:
                    return  # Saturation never changes automatic matching or membership.
                count = self.db.execute(
                    "SELECT COUNT(*) FROM review_queue WHERE ref=?", (ref,)
                ).fetchone()[0]
                if count >= policy.candidates_per_benchmark:
                    continue
                self.db.execute(
                    "INSERT INTO review_queue VALUES(?,?,?,?,?)",
                    (doc, ref, identity, matched, len(pattern)),
                )
                emitted += 1

    def summary(self) -> dict[str, int | str | bool]:
        # Detailed references and queue digest stay private: public strings can be guessed.
        count = int(self.db.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0])
        return {
            "enabled": self.policy.enabled,
            "disposition": self.policy.disposition,
            "candidates": count,
            "total_capacity_reached": count >= self.resources.review_candidates,
            "automatic_exclusions": 0,
        }

    def private_digest(self) -> str:
        return queue_digest(self.db)
