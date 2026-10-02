"""SQLite access without hidden external-sort files or corpus-sized SQL sorts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xlm.data.exclusion.policy import C05Error


class OrderedConnection(sqlite3.Connection):
    """Require index-backed reads; refuse a planner change that adds a sorter.

    All large indexes must be created on empty tables and maintained incrementally.
    Main database and rollback journal remain normal files under the job root.
    TEMP is memory-only as defense in depth; accepted reads cannot create temp
    B-trees, and automatic indexes are disabled. No process-global temp setting.
    """

    check: Callable[[], None] = staticmethod(lambda: None)

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        self.check()
        if sql.lstrip().upper().startswith("SELECT"):
            plan = super().execute("EXPLAIN QUERY PLAN " + sql, parameters)
            if any(
                "TEMP B-TREE" in str(row[3]).upper() or "AUTOMATIC" in str(row[3]).upper()
                for row in plan
            ):
                raise C05Error("SQL plan requires an unbounded temporary sort/index")
        return super().execute(sql, parameters)


def connect(
    path: Path, ceiling: int, check: Callable[[], None] = lambda: None
) -> OrderedConnection:
    if ceiling < 4096:
        raise C05Error("SQLite index ceiling is below one page")
    db = sqlite3.connect(path, factory=OrderedConnection)
    db.check = check
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA automatic_index=OFF")
    db.execute("PRAGMA mmap_size=0")
    db.execute("PRAGMA cache_size=-8192")
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    db.execute(f"PRAGMA max_page_count={max(1, ceiling // page_size)}")
    return db
