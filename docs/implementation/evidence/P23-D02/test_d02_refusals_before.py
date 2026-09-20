"""Explicit proposed refusal contract; original observation reproductions remain unchanged."""
from pathlib import Path

import pytest

from test_d02_before import PAYLOAD, fetcher


def test_corrupt_completed_original_is_refused_without_overwrite(tmp_path: Path) -> None:
    fetcher(tmp_path).run()
    cached = tmp_path / "output/rows.jsonl"
    corrupted = b"X" * len(PAYLOAD)
    cached.write_bytes(corrupted)
    with pytest.raises((ValueError, RuntimeError), match="checksum|integrity|corrupt"):
        fetcher(tmp_path).run()
    assert cached.read_bytes() == corrupted, "an existing completed original is not repaired in place"


def test_whole_file_over_record_cap_is_refused_before_publication(tmp_path: Path) -> None:
    with pytest.raises((ValueError, RuntimeError), match="record"):
        fetcher(tmp_path, records=1).run()
    assert not (tmp_path / "output/rows.jsonl").exists()
