"""A new partial has durable ownership before its first body read."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from test_acquisition_leases import AuthoredHandler, plan_for
from test_acquisition_leases import loopback as loopback
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.progress import ProgressJournal


def test_empty_prefix_is_owned_before_streaming_and_restartable(
    tmp_path: Path, loopback: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"authored-prefix" * 1000
    AuthoredHandler.files = {"payload.bin": payload}
    AuthoredHandler.cut_first_response_at = None
    plan = plan_for(loopback, ["payload.bin"])
    fetcher = BoundedFetcher(plan, tmp_path / "s", tmp_path / "o")

    def interrupted(*args: Any) -> int:
        state = ProgressJournal(
            fetcher.journal_path, plan.plan_id, plan.compute_behavioral_hash()
        ).state
        fp = state.file_progress["payload.bin"]
        assert fp.verified_prefix_bytes == 0
        assert fp.prefix_sha256 == hashlib.sha256(b"").hexdigest()
        raise KeyboardInterrupt("before body read")

    monkeypatch.setattr(fetcher, "_stream_body", interrupted)
    with pytest.raises(KeyboardInterrupt):
        fetcher.run()
    restarted = BoundedFetcher(plan, tmp_path / "s", tmp_path / "o")
    assert restarted.run().status == "COMPLETED"
    assert (tmp_path / "o/payload.bin").read_bytes() == payload
