"""Synthetic negative controls for the independent review's parsers and binding checks."""

import pytest
from review_result import ReviewError, canonical, digest, lines, loads, sealed


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}']
)
def test_strict_json_refuses_ambiguous_or_nonfinite_values(raw):
    with pytest.raises(ReviewError):
        loads(raw)


def test_binding_recomputes_body_digest(tmp_path):
    obj = {"synthetic": True, "records": 2}
    obj["digest"] = digest(obj)
    path = tmp_path / "binding.json"
    path.write_bytes(canonical(obj))
    assert sealed(path) == obj
    obj["records"] = 3
    path.write_bytes(canonical(obj))
    with pytest.raises(ReviewError, match="self-digest mismatch"):
        sealed(path)


@pytest.mark.parametrize(
    "raw,limit", [(b'{"x":1}\n{"x":2}\n', 1), (b'{"x": 1}\n', 1), (b'{"x":1}', 1)]
)
def test_jsonl_refuses_excess_or_noncanonical_records(tmp_path, raw, limit):
    path = tmp_path / "authored.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ReviewError):
        lines(path, limit)


def test_jsonl_exact_boundary_preserves_bytes(tmp_path):
    path = tmp_path / "authored.jsonl"
    raw = b'{"x":1}\n'
    path.write_bytes(raw)
    assert lines(path, 1) == [(raw, {"x": 1})]
