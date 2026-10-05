"""count-tokens full-lifecycle regressions (production AGGREGATE FileNotFoundError, b5eb4f8).

The first real run counted every record, then refused on entering AGGREGATE: the
staging directory ``<output>.partial-*`` was created without ``parents=True`` (the
reference creates its parents), so a not-yet-existing output parent failed only after
the whole source count. Every late filesystem operation is now probed before any
counting. Authored fixtures only; bounded (seconds to about a minute).
"""

# ruff: noqa: F811  (pytest fixtures imported from test_count_tokens_fast)

from __future__ import annotations

import json
import os
from collections import namedtuple
from pathlib import Path
from typing import Any

import psutil
import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV
from scripts.count_tokens_benchmark import build

from test_c06_tokenizer_fit import c05, key  # noqa: F401 (fixtures)
from test_count_tokens_fast import (  # noqa: F401 (fixtures)
    ARTIFACTS,
    artifacts,
    assert_nothing_published,
    cli,
    fast,
    reference,
    tok,
)
from xlm.data.exclusion import countfast
from xlm.data.exclusion.countfast import count_tokens_fast
from xlm.data.exclusion.fitscan import OrderedPool
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error


def no_residue(parent: Path) -> None:
    """No staging or preflight directory of this job survives (success or failure)."""
    assert not [p.name for p in parent.iterdir() if ".partial-" in p.name]


# -- the production regression ------------------------------------------------------------


@pytest.mark.parametrize("workers", ["1", "4"])
def test_missing_output_parent_is_created_like_the_reference(
    c05: dict[str, Any],
    tok: Path,
    reference: dict[str, Any],
    tmp_path: Path,
    workers: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """b5eb4f8 refused here with FileNotFoundError after the whole SOURCE COUNT."""
    out = tmp_path / "G" / "XLM" / "counts"  # no ancestor below tmp_path exists yet
    args = cli("count-tokens", c05, tok, tmp_path, "--workers", workers, "--no-progress")
    args[args.index("--output") + 1] = str(out / "mix01-clean-v1")
    capsys.readouterr()
    assert operator(args) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"digest", "mode"}
    assert artifacts(out / "mix01-clean-v1") == reference["bytes"]
    assert sorted(p.name for p in out.iterdir()) == ["mix01-clean-v1"]
    assert list((tmp_path / "scratch").iterdir()) == []


def test_output_preflight_refuses_in_seconds_before_any_counting(
    c05: dict[str, Any],
    tok: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    for name in ("stream_membership", "source_count", "snapshot_tokenizer"):
        original = getattr(countfast, name)

        def spy(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(countfast, name, spy)
    blocker = tmp_path / "not-a-directory"
    blocker.write_bytes(b"x")
    args = cli("count-tokens", c05, tok, tmp_path, "--workers", "1", "--no-progress")
    args[args.index("--output") + 1] = str(blocker / "counts")
    capsys.readouterr()
    assert operator(args) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"] is True and refusal["stage"] == "OUTPUT PREFLIGHT"
    assert str(tmp_path) not in json.dumps(refusal) and calls == []

    # Publication by hard link (write_once) unsupported on the output volume.
    def no_links(*_: Any) -> None:
        raise OSError(1, "hard links unsupported")

    monkeypatch.setattr(countfast.os, "link", no_links)
    with pytest.raises(OSError) as raised:
        fast(c05, tok, tmp_path / "nolink")
    assert getattr(raised.value, "count_stage", None) == "OUTPUT PREFLIGHT" and calls == []
    assert_nothing_published(tmp_path / "nolink")
    no_residue(tmp_path / "nolink")


def test_guaranteed_export_failure_refuses_before_source_count(
    c05: dict[str, Any], tok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counted: list[int] = []
    original = countfast.source_count

    def spy(*args: Any, **kwargs: Any) -> Any:
        counted.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(countfast, "source_count", spy)
    usage = namedtuple("usage", "total used free")
    real = countfast.shutil.disk_usage
    monkeypatch.setattr(countfast.shutil, "disk_usage", lambda _: usage(1, 1, 0))
    with pytest.raises(C05Error, match="lacks space") as raised:
        fast(c05, tok, tmp_path)
    assert getattr(raised.value, "count_stage", None) == "MEMBERSHIP VERIFY" and counted == []
    assert_nothing_published(tmp_path)
    no_residue(tmp_path)
    monkeypatch.setattr(countfast.shutil, "disk_usage", real)
    bounds: list[int] = []
    original_bound = countfast.export_lower_bound

    def bound(*args: Any) -> int:
        bounds.append(original_bound(*args))
        return bounds[-1]

    monkeypatch.setattr(countfast, "export_lower_bound", bound)
    envelope = fast(c05, tok, tmp_path / "ok")
    size = envelope["payload"]["counts_bytes"]
    assert 0 < bounds[0] <= size < bounds[0] * 1.1  # a true lower bound, and tight


def test_worker_teardown_never_invalidates_aggregation_inputs(
    c05: dict[str, Any],
    tok: Path,
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    original = countfast.aggregate

    def checked(*args: Any) -> Any:
        # Spawned workers are gone; aggregation inputs are the parent's own arrays.
        seen["children"] = [p.pid for p in psutil.Process().children(recursive=True)]
        seen["scratch"] = sorted(p.name.split("-")[1] for p in (tmp_path / "scratch").iterdir())
        seen["stage"] = [p.name for p in tmp_path.iterdir() if ".partial-" in p.name]
        return original(*args)

    monkeypatch.setattr(countfast, "aggregate", checked)
    fast(c05, tok, tmp_path, workers=4, inline=False)
    assert seen["children"] == [] and seen["scratch"] == ["tokenizer"]
    assert len(seen["stage"]) == 1  # the staging directory exists from the preflight on
    assert artifacts(tmp_path / "counts") == reference["bytes"]
    no_residue(tmp_path)
    assert list((tmp_path / "scratch").iterdir()) == []


def test_late_failure_and_interrupt_remove_the_early_staging_directory(
    c05: dict[str, Any], tok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n, error in enumerate((OSError(28, "disk full"), KeyboardInterrupt())):

        def broken(*_: Any, _error: BaseException = error) -> Any:
            raise _error

        monkeypatch.setattr(countfast, "export_counts", broken)
        out = tmp_path / str(n)
        with pytest.raises(type(error)) as raised:
            fast(c05, tok, out)
        if isinstance(error, OSError):
            assert getattr(raised.value, "count_stage", None) == "EXPORT COUNTS"
        assert_nothing_published(out)
        no_residue(out)


# -- thousands of result units through the whole lifecycle ---------------------------------


@pytest.fixture(scope="module")
def many(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Generated C05 chain (2,500 docs, production vocab) and its reference counts."""
    root = tmp_path_factory.mktemp("many") / "bench"
    os.environ[KEY_ENV] = KEY
    build(root, 2500, 20261005)
    out = root.parent / "ref"
    assert (
        operator(
            [
                "count-tokens-reference",
                "--c05-proof",
                str(root / "proof.json"),
                "--tokenizer",
                str(root / "tokenizer"),
                "--scratch",
                str(out / "scratch"),
                "--output",
                str(out / "counts"),
                "--issuer",
                ISSUER,
                "--key-env",
                KEY_ENV,
                "--no-progress",
            ]
        )
        == 0
    )
    return {"root": root, "bytes": artifacts(out / "counts")}


@pytest.mark.parametrize(("workers", "inline"), [(1, True), (4, False), (16, False)])
def test_thousands_of_result_units_aggregate_byte_identically(
    many: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
    inline: bool,
) -> None:
    monkeypatch.setattr(countfast, "SPLIT_TEXT_BYTES", 0)  # every file chunked
    monkeypatch.setattr(countfast, "CHUNK_SPAN_BYTES", 1)  # one kept row per chunk
    units: list[str] = []
    original = OrderedPool.submit

    def spy(self: OrderedPool, function: Any, task: Any) -> Any:
        units.append(type(task).__name__)
        return original(self, function, task)

    monkeypatch.setattr(OrderedPool, "submit", spy)
    out = tmp_path / "missing" / "parent"
    count_tokens_fast(
        many["root"] / "proof.json",
        many["root"] / "tokenizer",
        out / "counts",
        ISSUER,
        KEY.encode(),
        scratch=tmp_path / "scratch",
        workers=workers,
        inline=inline,
    )
    assert units.count("ChunkTask") > 2000
    assert artifacts(out / "counts") == many["bytes"]
    assert sorted(p.name for p in out.iterdir()) == ["counts"]
    assert list((tmp_path / "scratch").iterdir()) == []
    assert ARTIFACTS == ("counts.jsonl", "counts.json")
