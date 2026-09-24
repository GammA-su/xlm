"""Independent journal freshness regression on Windows."""

from pathlib import Path

from test_acquisition_leases import manager


def test_journal_inplace_edit_with_preserved_stat_is_not_hidden(tmp_path: Path) -> None:
    import os

    capacity = manager(tmp_path)
    journal = capacity.journal
    assert journal is not None
    with journal.transaction() as state:
        state.cache_hits = 1
    path = journal.journal_path
    before = path.stat()
    payload = path.read_bytes().replace(b'"cache_hits":1', b'"cache_hits":9')
    path.write_bytes(payload)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ino == before.st_ino
    with journal.transaction(persist=False) as state:
        assert state.cache_hits == 9, "same stat tuple hid externally modified journal bytes"
