"""Disk-backed C05 facts, duplicate sets and known-lineage groups.

No corpus-sized Python collections. SQLite owns sorting, membership and union-find;
candidate lists are bounded by the frozen bucket and per-document caps.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from pathlib import Path

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.lineage import lineage_keys_v2
from xlm.data.dedup.minhash import MinHasher, estimated_jaccard, stable_hash64
from xlm.data.evidence_v2.canonical import digest
from xlm.data.exclusion.policy import C05Error, ProductionPolicy, Resources
from xlm.data.exclusion.storage import connect


class DiskGroups:
    def __init__(
        self,
        path: Path,
        policy: ProductionPolicy,
        resources: Resources,
        check: Callable[[], None] = lambda: None,
    ) -> None:
        self.policy, self.resources = policy, resources
        self.hasher = MinHasher(policy.minhash())
        self.db = connect(path, resources.index_bytes, check)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, sha TEXT NOT NULL,
                                             attestation TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS docs(
              id TEXT PRIMARY KEY, source TEXT, component TEXT, view TEXT,
              file TEXT, row INTEGER, content TEXT, bytes INTEGER,
              exact TEXT, signature BLOB, hit TEXT,
              duplicate TEXT, family TEXT, survivor INTEGER DEFAULT 0, upstream TEXT);
            CREATE INDEX IF NOT EXISTS exact_idx ON docs(exact,id);
            CREATE INDEX IF NOT EXISTS doc_file ON docs(file,row,id);
            CREATE INDEX IF NOT EXISTS dup_order ON docs(duplicate,bytes DESC,source,id);
            CREATE INDEX IF NOT EXISTS family_idx ON docs(family);
            CREATE TABLE IF NOT EXISTS bands(key TEXT, id TEXT, PRIMARY KEY(key,id));
            CREATE TABLE IF NOT EXISTS lineage(key TEXT, id TEXT, PRIMARY KEY(key,id));
            CREATE INDEX IF NOT EXISTS band_doc ON bands(id,key);
            CREATE INDEX IF NOT EXISTS lineage_doc ON lineage(id,key);
            CREATE TABLE IF NOT EXISTS families(
              id TEXT PRIMARY KEY, ordering TEXT, bytes INTEGER, hit INTEGER,
              split TEXT, quick INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS family_order ON families(ordering,id);
            CREATE TABLE IF NOT EXISTS stats(key TEXT PRIMARY KEY, value INTEGER);
            CREATE TABLE IF NOT EXISTS seals(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def add(
        self,
        doc: CanonicalDocument,
        *,
        file: str,
        row: int,
        component: str,
        view: str,
        tokens: list[str],
        hit: str | None,
        upstream: str | None = None,
    ) -> None:
        if (
            self.policy.gutenberg == "require_book_ids"
            and doc.source_metadata.get("upstream_component") == "project_gutenberg"
            and not doc.source_metadata.get("book_id")
        ):
            raise C05Error("Gutenberg whole-book policy requires real book identity")
        # The existing vectorized implementation allocates permutations x shingles.
        # Chunk shingle hashes here to bound that temporary independently of document size.
        best: list[int] | None = None
        size = self.policy.shingle_size
        starts = range(max(1, len(tokens) - size + 1))
        chunk: set[int] = set()
        for start in starts:
            chunk.add(stable_hash64(" ".join(tokens[start : start + size])))
            if len(chunk) == 2048:
                sig = self.hasher.signature_from_hashes(chunk)
                best = sig if best is None else [min(a, b) for a, b in zip(best, sig, strict=True)]
                chunk.clear()
        if chunk:
            sig = self.hasher.signature_from_hashes(chunk)
            best = sig if best is None else [min(a, b) for a, b in zip(best, sig, strict=True)]
        if best is None:
            best = self.hasher.signature_from_tokens([])
        packed = struct.pack(f"<{len(best)}Q", *best)
        self.db.execute(
            "INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (
                doc.doc_id,
                doc.source_id,
                component,
                view,
                file,
                row,
                digest(doc.to_dict()),
                doc.utf8_byte_count,
                hashlib.sha256(" ".join(tokens).encode()).hexdigest(),
                packed,
                hit,
                doc.doc_id,
                doc.doc_id,
                upstream,
            ),
        )
        self.db.executemany(
            "INSERT INTO bands VALUES(?,?)",
            ((key, doc.doc_id) for key in self.hasher.band_keys(best)),
        )
        self.db.executemany(
            "INSERT INTO lineage VALUES(?,?)", ((key, doc.doc_id) for key in lineage_keys_v2(doc))
        )

    def root(self, doc: str, column: str) -> str:
        if column not in {"duplicate", "family"}:
            raise C05Error("invalid group column")
        original = doc
        while True:
            result = self.db.execute(f"SELECT {column} FROM docs WHERE id=?", (doc,)).fetchone()
            if result is None:
                raise C05Error("missing group parent")
            parent = str(result[0])
            if parent == doc:
                break
            if parent >= doc:
                raise C05Error("corrupt union-find ordering")
            doc = parent
        self.db.execute(f"UPDATE docs SET {column}=? WHERE id=?", (doc, original))
        return doc

    def union(self, a: str, b: str, column: str) -> None:
        left, right = sorted((self.root(a, column), self.root(b, column)))
        if left != right:
            self.db.execute(f"UPDATE docs SET {column}=? WHERE id=?", (left, right))

    def group(
        self, check: Callable[[], None], spend_comparison: Callable[[], None]
    ) -> dict[str, int]:
        # One transaction: interruption rolls back the entire grouping stage.
        # Its elapsed-time budget lives in the runner journal and is never reset.
        counts = {"comparisons": 0, "oversized_bands": 0, "candidate_cap_documents": 0}
        self.db.execute("UPDATE docs SET duplicate=id,family=id,survivor=0")
        self.db.execute("DELETE FROM families")
        previous: tuple[str, str] | None = None
        for exact, doc in self.db.execute("SELECT exact,id FROM docs ORDER BY exact,id"):
            check()
            if previous and previous[0] == exact:
                self.union(previous[1], doc, "duplicate")
            previous = (exact, doc)
        for doc, packed in self.db.execute("SELECT id,signature FROM docs ORDER BY id"):
            check()
            sig = list(struct.unpack(f"<{self.policy.permutations}Q", packed))
            candidates: set[str] = set()
            capped = False
            for key in self.hasher.band_keys(sig):
                rows = self.db.execute(
                    "SELECT id FROM bands WHERE key=? ORDER BY id LIMIT ?",
                    (key, self.policy.max_bucket_size + 1),
                ).fetchall()
                if len(rows) > self.policy.max_bucket_size:
                    counts["oversized_bands"] += 1
                    continue
                for (other,) in rows:
                    if other >= doc or other in candidates:
                        continue
                    if len(candidates) == self.policy.max_candidates_per_document:
                        capped = True
                        break
                    candidates.add(other)
                if capped:
                    break
            counts["candidate_cap_documents"] += int(capped)
            for other in sorted(candidates):
                spend_comparison()
                counts["comparisons"] += 1
                if counts["comparisons"] > self.resources.comparisons:
                    raise C05Error("candidate comparison ceiling")
                raw = self.db.execute("SELECT signature FROM docs WHERE id=?", (other,)).fetchone()[
                    0
                ]
                other_sig = list(struct.unpack(f"<{self.policy.permutations}Q", raw))
                if estimated_jaccard(sig, other_sig) >= self.policy.near_threshold:
                    self.union(doc, other, "duplicate")
        previous = None
        for key, doc in self.db.execute("SELECT key,id FROM lineage ORDER BY key,id"):
            check()
            if previous and previous[0] == key:
                self.union(previous[1], doc, "family")
            previous = (key, doc)
        for (doc,) in self.db.execute("SELECT id FROM docs ORDER BY id"):
            check()
            self.union(doc, self.root(doc, "duplicate"), "family")
        for (doc,) in self.db.execute("SELECT id FROM docs ORDER BY id"):
            check()
            self.root(doc, "duplicate")
            self.root(doc, "family")
        last = None
        for dup, doc in self.db.execute(
            "SELECT duplicate,id FROM docs ORDER BY duplicate,bytes DESC,source,id"
        ):
            check()
            if dup != last:
                self.db.execute("UPDATE docs SET survivor=1 WHERE id=?", (doc,))
            last = dup
        for family, size, hit in self.db.execute(
            "SELECT family,SUM(CASE WHEN survivor=1 THEN bytes ELSE 0 END),"
            "MAX(hit IS NOT NULL) FROM docs GROUP BY family"
        ):
            check()
            self.db.execute(
                "INSERT INTO families VALUES(?,?,?,?,?,0)",
                (family, digest([self.policy.seed, family]), size, hit, "train"),
            )
        diagnostic = audit = quick = 0
        for family, size in self.db.execute(
            "SELECT id,bytes FROM families WHERE hit=0 ORDER BY ordering,id"
        ):
            check()
            split, is_quick = "train", 0
            if diagnostic < self.policy.diagnostic_bytes:
                split = "diagnostic_val"
                diagnostic += size
                if quick < self.policy.quick_bytes:
                    is_quick = 1
                    quick += size
            elif audit < self.policy.audit_bytes:
                split = "audit"
                audit += size
            self.db.execute(
                "UPDATE families SET split=?,quick=? WHERE id=?", (split, is_quick, family)
            )
        self.db.executemany("INSERT OR REPLACE INTO stats VALUES(?,?)", counts.items())
        return counts

    def close(self) -> None:
        self.db.close()

    def facts_digest(self, file: str, check: Callable[[], None]) -> str:
        """Authenticate immutable facts and all derived postings/lineage on reuse."""
        from xlm.data.evidence_v2.canonical import canonical_bytes

        value = hashlib.sha256()
        query = (
            "SELECT id,source,component,view,file,row,content,bytes,exact,signature,hit,upstream "
            "FROM docs WHERE file=? ORDER BY row,id"
        )
        for record in self.db.execute(query, (file,)):
            check()
            row = list(record)
            row[9] = row[9].hex()
            value.update(canonical_bytes(row))
            for table in ("bands", "lineage"):
                for (key,) in self.db.execute(
                    f"SELECT key FROM {table} WHERE id=? ORDER BY key", (row[0],)
                ):
                    value.update(canonical_bytes([table, key]))
        return value.hexdigest()

    def group_digest(self, check: Callable[[], None]) -> str:
        from xlm.data.evidence_v2.canonical import canonical_bytes

        value = hashlib.sha256()
        for query in (
            "SELECT id,duplicate,family,survivor FROM docs ORDER BY id",
            "SELECT * FROM families ORDER BY id",
            "SELECT * FROM stats ORDER BY key",
        ):
            for row in self.db.execute(query):
                check()
                value.update(canonical_bytes(row))
        return value.hexdigest()
