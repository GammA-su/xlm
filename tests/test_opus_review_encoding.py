"""Splicing must fail closed when its source record changes."""

from typing import Any

import pytest

from test_selected_record_encoding import legacy_selected
from xlm.data.acquisition.records import encode_record, selected_record


@pytest.mark.parametrize(
    "change", ["scalar", "nested", "numeric_type", "signed_zero", "key", "nested_key"]
)
def test_splice_falls_back_after_source_mutation(change: str) -> None:
    record: dict[str, Any] = {"text": "original", "values": [1, {"x": 0.0}], "mapping": {1: "x"}}
    raw = encode_record(record)
    if change == "scalar":
        record["text"] = "changed"
    elif change == "nested":
        record["values"][1]["x"] = 123
    elif change == "numeric_type":
        record["values"][0] = True
    elif change == "signed_zero":
        record["values"][1]["x"] = -0.0
    elif change == "nested_key":
        record["mapping"] = {1.0: "x"}
    else:
        del record["text"]
        record["z"] = "renamed"
    assert selected_record(record, {}, raw) == legacy_selected(record, {}, raw)
