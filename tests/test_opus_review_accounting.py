"""Independent authored regressions for the Opus accounting review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_acquisition_leases import AuthoredHandler, plan_for
from test_acquisition_leases import loopback as loopback
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.progress import ProgressJournal


def test_skipped_selected_records_are_durably_reserved_before_parse(
    tmp_path: Path, loopback: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    AuthoredHandler.files = {"rows.jsonl": b'{"text":"row"}\n' * 100}
    AuthoredHandler.cut_first_response_at = None
    plan = plan_for(
        loopback, ["rows.jsonl"], mode="selected_records", row_ranges={"rows.jsonl": (70, 80)}
    )
    fetcher = BoundedFetcher(plan, tmp_path / "s", tmp_path / "o")
    original = json.loads
    seen = 0

    def observe(raw: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal seen
        if raw == b'{"text":"row"}\n':
            seen += 1
            # Independent disk read, not the fetcher's in-memory accounting.
            state = ProgressJournal(
                fetcher.journal_path, plan.plan_id, plan.compute_behavioral_hash()
            ).state
            charged = state.accounting.consumed.get("records_scanned", 0)
            reserved = sum(state.accounting.reservations.get("records_scanned", {}).values())
            assert charged + reserved >= seen, "record parsed before durable scan reservation"
        return original(raw, *args, **kwargs)

    monkeypatch.setattr("xlm.data.acquisition.selection.json.loads", observe)
    state = fetcher.run()
    assert seen == 80
    assert state.accounting.consumed["records_scanned"] == 80
    assert not state.accounting.reservations["records_scanned"]
