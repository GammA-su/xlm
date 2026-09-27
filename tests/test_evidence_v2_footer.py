"""Evidence-v2.0 live footer/cost planning: authored fixtures only.

No network (sockets blocked; the live transport is exercised through a
stub opener, never a socket), no X: reads, no real acquisition. Covers
frozen-file gating, footer-only ranges, deterministic v2 windows, every
budget/refusal/drift/feasibility behavior, no-rerank, atomic failure
receipts, and metadata-only Arm-T cost planning.
"""

from __future__ import annotations

import hashlib
import io
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.evidence_v2 import budgets, canonical, footer, frozen, receipts, text_costs
from xlm.data.sources.transport import (
    HostNotAllowlistedError,
    TransportBudget,
    validate_host,
)

WINNERS = [
    frozen.crawl_path(c, s[3]) for c, s in zip(frozen.CRAWL_ORDER, frozen.STRATA, strict=True)
]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _distinct_payloads(prefix: str, n: int, size: int) -> list[str]:
    # Per-row, per-block distinct pseudo-random hex text: deterministic,
    # ~ratio 1, immune to dictionary/RLE collapse in bulk-size tests.
    blocks = size // 64 + 1
    return [
        "".join(hashlib.sha256(f"{prefix}-{i}-{b}".encode()).hexdigest() for b in range(blocks))[
            :size
        ]
        for i in range(n)
    ]


def _table(
    n: int,
    taxonomy_payload: str = "x",
    with_text: bool = True,
    *,
    bulk_taxonomy_bytes: int = 0,
    bulk_text_bytes: int = 0,
) -> pa.Table:
    tax_values: list[str] = (
        _distinct_payloads("tax", n, bulk_taxonomy_bytes)
        if bulk_taxonomy_bytes
        else [taxonomy_payload] * n
    )
    tax = pa.StructArray.from_arrays(
        [pa.array(tax_values, type=pa.string())],
        names=["code"],
    )
    sig = pa.StructArray.from_arrays(
        [pa.array([0.9] * n, type=pa.float64())],
        names=["english"],
    )
    columns = [
        pa.field("eai_taxonomy", tax.type),
        pa.field("quality_signals", sig.type),
    ]
    arrays: list[Any] = [tax, sig]
    if with_text:
        bodies = _distinct_payloads("body", n, bulk_text_bytes) if bulk_text_bytes else ["body"] * n
        text = pa.array(bodies, type=pa.string())
        columns.append(pa.field("text", pa.string()))
        arrays.append(text)
    return pa.Table.from_arrays(arrays, schema=pa.schema(columns))


def _parquet_bytes(groups: list[pa.Table]) -> bytes:
    sink = io.BytesIO()
    writer = pq.ParquetWriter(sink, groups[0].schema)
    for table in groups:
        writer.write_table(table)
    writer.close()
    return sink.getvalue()


def _good_image(groups: tuple[int, ...] = (600, 600, 600)) -> footer.FakeImage:
    # ~2 KB distinct taxonomy payloads keep fixtures at multi-MB scale so
    # footer tail reads are geometrically separated from data pages.
    return footer.FakeImage(_parquet_bytes([_table(n, bulk_taxonomy_bytes=2048) for n in groups]))


def _images(names: list[str], **overrides: Any) -> dict[str, footer.FakeImage]:
    images = {name: _good_image() for name in names}
    images.update(overrides)
    return images


def _assert_footer_only(calls: list[tuple[str, int, int]], content: bytes) -> None:
    # Every served range is the 4-byte magic or sits inside the trailing
    # 64 KiB footer zone; the served-bytes total stays far below the file.
    total = sum(hi - lo + 1 for _, lo, hi in calls)
    assert total < len(content) // 10, (total, len(content))
    for _, lo, hi in calls:
        assert (lo == 0 and hi == 3) or lo >= len(content) - 65536, (lo, hi)


def test_exactly_eight_frozen_files() -> None:
    assert WINNERS == [
        "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet",
        "data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet",
        "data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet",
        "data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet",
        "data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet",
        "data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet",
        "data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet",
        "data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet",
    ]


def test_non_frozen_file_refused() -> None:
    transport = footer.FakeFooterTransport(_images(WINNERS))
    with pytest.raises(footer.FooterError, match="not one of the eight frozen"):
        footer.plan_one_file(
            "data/crawl=CC-MAIN-2014-15/train-00000-of-02772.parquet",
            transport=transport,
            ledger=budgets.new_arm_m(),
            winners=WINNERS,
        )
    assert transport.calls == []


def test_footer_only_ranges_and_window() -> None:
    images = _images(WINNERS)
    transport = footer.FakeFooterTransport(images)
    ledger = budgets.new_arm_m()
    unit = footer.plan_one_file(WINNERS[0], transport=transport, ledger=ledger, winners=WINNERS)
    assert unit["retained_rows"] == 512
    assert unit["absolute_window"][1] - unit["absolute_window"][0] == 512
    assert unit["absolute_window"][0] == unit["group_start"] + unit["relative_window_start"]
    assert unit["future_plan_feasible"] is True
    assert unit["row_group_count"] == 3
    assert all(g["eligible"] for g in unit["row_groups"])
    assert "text" not in " ".join(unit["projected_physical_leaves"])
    assert unit["remote_length"] == len(images[WINNERS[0]].content)
    _assert_footer_only(transport.calls, images[WINNERS[0]].content)
    assert ledger.snapshot()["requests"] > 0
    assert ledger.snapshot()["response_body_bytes"] > 0


def test_deterministic_window_reproduction() -> None:
    first = footer.plan_one_file(
        WINNERS[1],
        transport=footer.FakeFooterTransport(_images(WINNERS)),
        ledger=budgets.new_arm_m(),
        winners=WINNERS,
    )
    second = footer.plan_one_file(
        WINNERS[1],
        transport=footer.FakeFooterTransport(_images(WINNERS)),
        ledger=budgets.new_arm_m(),
        winners=WINNERS,
    )
    assert first["absolute_window"] == second["absolute_window"]
    assert first["selected_group"] == second["selected_group"]
    assert canonical.digest(first) == canonical.digest(second)


def test_arm_complete_receipt_deterministic(tmp_path: Path) -> None:
    def run() -> dict[str, Any]:
        return footer.plan_arm_m(
            footer.FakeFooterTransport(_images(WINNERS)), budgets.new_arm_m(), WINNERS
        )

    first, second = run(), run()
    assert first["status"] == "COMPLETE" and len(first["units"]) == 8
    assert canonical.digest(first) == canonical.digest(second)
    binding = receipts.publish_manifest(tmp_path / "footer_evidence.json", first)
    assert binding["bytes"] > 0


def test_missing_file_stops_arm_without_rerank() -> None:
    images = _images(WINNERS)
    del images[WINNERS[2]]
    transport = footer.FakeFooterTransport(images)
    with pytest.raises(footer.ArmIncomplete) as caught:
        footer.plan_arm_m(transport, budgets.new_arm_m(), WINNERS)
    assert caught.value.failed_file == WINNERS[2]
    assert len(caught.value.units) == 2
    assert all(c[0] == WINNERS[2] or c[0] in WINNERS[:2] for c in transport.calls)
    assert transport.calls and transport.calls[0][0] == WINNERS[0]


def test_schema_mismatch_refuses() -> None:
    schema = pa.schema([pa.field("other", pa.string())])
    sink = io.BytesIO()
    with pq.ParquetWriter(sink, schema) as writer:
        writer.write_table(pa.Table.from_arrays([pa.array(["a"] * 600)], schema=schema))
    images = _images(WINNERS, **{WINNERS[0]: footer.FakeImage(sink.getvalue())})
    with pytest.raises(footer.ArmIncomplete) as caught:
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)
    assert caught.value.failed_file == WINNERS[0]


def test_missing_projected_field_refuses() -> None:
    table = pa.Table.from_arrays(
        [pa.array(["x"] * 600)],
        schema=pa.schema([pa.field("eai_taxonomy", pa.string())]),
    )
    images = _images(WINNERS, **{WINNERS[0]: footer.FakeImage(_parquet_bytes([table]))})
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)


def test_short_file_512_refusal_no_rerank() -> None:
    images = _images(WINNERS, **{WINNERS[3]: _good_image((100,))})
    transport = footer.FakeFooterTransport(images)
    with pytest.raises(footer.ArmIncomplete) as caught:
        footer.plan_arm_m(transport, budgets.new_arm_m(), WINNERS)
    assert caught.value.failed_file == WINNERS[3]
    assert "not 512" in caught.value.reason
    assert {c[0] for c in transport.calls} <= set(WINNERS[:4])


def test_future_plan_infeasible_refuses() -> None:
    # 600 distinct ~120 KB taxonomy values (~72 MB uncompressed, ratio ~1):
    # passes pilot gates but trips the 64 MiB/file future-decompressed bound.
    images = _images(
        WINNERS,
        **{WINNERS[0]: footer.FakeImage(_parquet_bytes([_table(600, bulk_taxonomy_bytes=120000)]))},
    )
    with pytest.raises(footer.ArmIncomplete) as caught:
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)
    assert caught.value.failed_file == WINNERS[0]
    assert "64 MiB" in caught.value.reason


def test_future_feasibility_branches() -> None:
    class Chosen:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(
                {
                    "domain_estimated_requests": 0,
                    "estimated_requests": 0,
                    "domain_estimated_transfer_upper_bytes": 0,
                    "estimated_transfer_upper_bytes": 0,
                    "domain_estimated_scan_uncompressed_bytes": 0,
                    "estimated_scan_uncompressed_bytes": 0,
                    "domain_scan_rows": 0,
                    "expected_scan_rows": 0,
                }
            )
            self.__dict__.update(kwargs)

    good = footer._future_feasibility(
        Chosen(
            domain_estimated_requests=10,
            domain_estimated_transfer_upper_bytes=1000,
            domain_estimated_scan_uncompressed_bytes=1000,
            domain_scan_rows=512,
        )
    )
    assert good["feasible"] is True
    bad_requests = footer._future_feasibility(Chosen(domain_estimated_requests=101))
    assert bad_requests["feasible"] is False
    bad_scan = footer._future_feasibility(Chosen(domain_scan_rows=16385))
    assert bad_scan["feasible"] is False
    bad_memory = footer._future_feasibility(
        Chosen(domain_estimated_scan_uncompressed_bytes=250000000)
    )
    assert bad_memory["feasible"] is False
    assert any("reservation" in r for r in bad_memory["reasons"])
    no_bound = footer._future_feasibility(Chosen())
    assert no_bound["feasible"] is False


def test_file_identity_drift_refuses() -> None:
    image = _good_image()
    image.drift_total_on_call = 2
    images = _images(WINNERS, **{WINNERS[0]: image})
    with pytest.raises(footer.ArmIncomplete) as caught:
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)
    assert "drift" in caught.value.reason


def test_etag_drift_refuses() -> None:
    image = _good_image()
    image.drift_etag_on_call = 2
    images = _images(WINNERS, **{WINNERS[0]: image})
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)


def test_per_file_request_refusal() -> None:
    ledger = budgets.new_arm_m()
    for _ in range(10):
        ledger.charge_file_request(WINNERS[0], kind="footer")
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(_images(WINNERS)), ledger, WINNERS)


def test_arm_request_refusal() -> None:
    ledger = budgets.new_arm_m()
    for _ in range(79):
        ledger.charge_request(kind="footer")
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(_images(WINNERS)), ledger, WINNERS)


def test_per_file_bytes_refusal() -> None:
    ledger = budgets.new_arm_m()
    ledger.charge_transfer(WINNERS[0], 4194304, kind="footer")
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(_images(WINNERS)), ledger, WINNERS)


def test_arm_bytes_refusal() -> None:
    ledger = budgets.new_arm_m()
    for _ in range(8):
        ledger.charge_response_body(4194304, kind="footer")
    with pytest.raises(footer.ArmIncomplete):
        footer.plan_arm_m(footer.FakeFooterTransport(_images(WINNERS)), ledger, WINNERS)


def test_failure_receipt_no_success_artifact(tmp_path: Path) -> None:
    images = _images(WINNERS)
    del images[WINNERS[0]]
    try:
        footer.plan_arm_m(footer.FakeFooterTransport(images), budgets.new_arm_m(), WINNERS)
        raise AssertionError("expected ArmIncomplete")
    except footer.ArmIncomplete as exc:
        receipt = receipts.seal(footer.incomplete_receipt(exc, command="plan-footers"))
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["failed_file"] == WINNERS[0]
    assert receipt["completed_units"] == []
    target = tmp_path / "footer_evidence.incomplete.json"
    receipts.publish_manifest(target, receipt)
    assert not (tmp_path / "footer_evidence.json").exists()
    assert receipt["digest"] == canonical.self_digest(
        {k: v for k, v in receipt.items() if k != "digest"}
    )


def test_resume_cannot_reset_shared_ledger() -> None:
    ledger = budgets.new_arm_m()
    transport = footer.FakeFooterTransport(_images(WINNERS))
    footer.plan_one_file(WINNERS[0], transport=transport, ledger=ledger, winners=WINNERS)
    used = ledger.snapshot()["requests"]
    assert used > 0
    footer.plan_one_file(WINNERS[1], transport=transport, ledger=ledger, winners=WINNERS)
    assert ledger.snapshot()["requests"] > used


def test_revision_pinned_in_transport() -> None:
    transport = footer.FakeFooterTransport({})
    url = transport.canonical_url(WINNERS[0])
    assert frozen.REVISION in url
    assert url.startswith("https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/")
    live = footer.LiveFooterTransport.__new__(footer.LiveFooterTransport)
    assert live.canonical_url.__doc__ is not None


# --------------------------------------------------------------------------
# Live transport units through a stub opener (no sockets).
# --------------------------------------------------------------------------


class _StubResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 206,
        total: int | None = None,
        etag: str | None = "e",
        url: str = "https://huggingface.co/x",
    ) -> None:
        self._body = body
        self.status = status
        self.headers = {
            "Content-Range": f"bytes 0-{len(body) - 1}/{total if total is not None else len(body)}",
            "Content-Length": str(len(body)),
            "ETag": etag,
        }
        if etag is None:
            del self.headers["ETag"]
        self._url = url

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._body = self._body, b""
        else:
            chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _StubOpener:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.urls: list[str] = []

    def open(self, request: Any, timeout: float | None = None) -> Any:
        self.urls.append(request.full_url)
        action = self.script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _live(script: list[Any]) -> tuple[footer.LiveFooterTransport, _StubOpener]:
    budget = TransportBudget(max_bytes=33554432, max_requests=80)
    opener = _StubOpener(script)
    transport = footer.LiveFooterTransport(
        budget, sleep=lambda _: None, opener_factory=lambda _: opener
    )
    return transport, opener


def test_live_retry_then_success() -> None:
    opener_script: list[Any] = [
        TimeoutError("timeout"),
        urllib.error.HTTPError("u", 503, "x", {}, None),
        _StubResponse(b"PAR1", total=100),
    ]
    transport, _ = _live(opener_script)
    evidence = transport.fetch_range(WINNERS[0], 0, 3)
    assert evidence.body == b"PAR1" and evidence.total_length == 100
    assert transport.budget.requests_made == 3


def test_live_gives_up_after_retries() -> None:
    transport, _ = _live([TimeoutError("t")] * 3)
    with pytest.raises(footer.FooterError, match="after 3 attempt"):
        transport.fetch_range(WINNERS[0], 0, 3)
    assert transport.budget.requests_made == 3


def test_live_no_retry_on_client_error() -> None:
    transport, opener = _live([urllib.error.HTTPError("u", 404, "x", {}, None)])
    with pytest.raises(footer.FooterError, match="after 1 attempt"):
        transport.fetch_range(WINNERS[0], 0, 3)
    assert len(opener.urls) == 1


def test_live_refuses_non_206_and_short_body() -> None:
    transport, _ = _live([_StubResponse(b"PAR1", status=200, total=100)])
    with pytest.raises(footer.FooterError, match="refused by server"):
        transport.fetch_range(WINNERS[0], 0, 3)


def test_live_response_body_cap_without_network() -> None:
    transport, opener = _live([])
    with pytest.raises(footer.FooterError, match="4 MiB body cap"):
        transport.fetch_range(WINNERS[0], 0, 4194304)
    assert opener.urls == []


def test_live_url_pins_revision_and_host() -> None:
    transport, opener = _live([_StubResponse(b"PAR1", total=100)])
    transport.fetch_range(WINNERS[4], 0, 3)
    assert frozen.REVISION in opener.urls[0]
    assert opener.urls[0].startswith("https://huggingface.co/")
    validate_host(opener.urls[0])


def test_redirect_hop_ceiling() -> None:
    budget = TransportBudget(max_bytes=33554432, max_requests=80)
    handler = footer.HopCountingRedirectHandler(budget)
    request = urllib.request.Request("https://huggingface.co/datasets/x")
    fp = _StubResponse(b"")
    for _ in range(3):
        handler.redirect_request(request, fp, 302, "m", {}, "https://cas-bridge.xethub.hf.co/y")
    with pytest.raises(footer.FooterError, match="3-hop"):
        handler.redirect_request(request, fp, 302, "m", {}, "https://cas-bridge.xethub.hf.co/y")


def test_redirect_evil_target_refused() -> None:
    budget = TransportBudget(max_bytes=33554432, max_requests=80)
    handler = footer.HopCountingRedirectHandler(budget)
    request = urllib.request.Request("https://huggingface.co/datasets/x")
    fp = _StubResponse(b"")
    with pytest.raises(HostNotAllowlistedError, match="not in the allowlist"):
        handler.redirect_request(request, fp, 302, "m", {}, "https://evil.example/z")


def test_host_allowlist_exact() -> None:
    validate_host("https://huggingface.co/datasets/x")
    validate_host("https://cas-bridge.xethub.hf.co/y?sig=z")
    with pytest.raises(HostNotAllowlistedError):
        validate_host("https://evil.example/x")
    with pytest.raises(HostNotAllowlistedError):
        validate_host("http://huggingface.co/x")
    with pytest.raises(HostNotAllowlistedError):
        validate_host("https://1.2.3.4/x")


# --------------------------------------------------------------------------
# Arm-T metadata-only cost planning.
# --------------------------------------------------------------------------


def _t_manifest() -> dict[str, Any]:
    manifest = {
        "kind": "essential_web_evidence_v2_text_selection",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "total_selected": 3,
        "cells": [
            {
                "stratum": "B_science_census",
                "requested": 29,
                "eligible": 29,
                "conflicts": 0,
                "selected": 3,
                "shortfall": 0,
                "identities": [
                    [frozen.REPOSITORY, frozen.REVISION, "dev-a.parquet", 10],
                    [frozen.REPOSITORY, frozen.REVISION, "dev-a.parquet", 500],
                    [frozen.REPOSITORY, frozen.REVISION, "dev-b.parquet", 5],
                ],
            }
        ],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    return manifest


def test_text_costs_metadata_only() -> None:
    manifest = _t_manifest()
    images = {
        "dev-a.parquet": _good_image((600, 600)),
        "dev-b.parquet": _good_image((600,)),
    }
    transport = footer.FakeFooterTransport(images)
    ledger = budgets.new_arm_t()
    result = text_costs.plan_text_costs(manifest, transport=transport, ledger=ledger)
    assert result["status"] == "COMPLETE"
    assert result["total_selected"] == 3
    assert len(result["files"]) == 2
    assert result["files"][0]["text_costs"]["text_leaf"] == "text"
    for name, image in images.items():
        _assert_footer_only([c for c in transport.calls if c[0] == name], image.content)
    # The authored document bodies never appear in cost evidence.
    import json as _json

    assert '"body"' not in _json.dumps(result)


def test_text_costs_locator_membership_only() -> None:
    manifest = _t_manifest()
    transport = footer.FakeFooterTransport(
        {
            "dev-a.parquet": _good_image((600, 600)),
            "dev-b.parquet": _good_image((600,)),
        }
    )
    result = text_costs.plan_text_costs(manifest, transport=transport, ledger=budgets.new_arm_t())
    wanted = {u["file"]: u["wanted_rows"] for u in result["files"]}
    assert wanted == {"dev-a.parquet": [10, 500], "dev-b.parquet": [5]}
    assert result["files"][0]["requests_upper"] <= 100


def test_text_costs_refusals() -> None:
    manifest = _t_manifest()
    bad = dict(manifest)
    bad["digest"] = "0" * 64
    with pytest.raises(text_costs.TextCostError, match="digest mismatch"):
        text_costs.plan_text_costs(
            bad,
            transport=footer.FakeFooterTransport({}),
            ledger=budgets.new_arm_t(),
        )
    over = dict(manifest)
    over["total_selected"] = 119
    over["digest"] = canonical.self_digest({k: v for k, v in over.items() if k != "digest"})
    with pytest.raises(text_costs.TextCostError, match="exceeds 118"):
        text_costs.plan_text_costs(
            over,
            transport=footer.FakeFooterTransport({}),
            ledger=budgets.new_arm_t(),
        )
    foreign = canonical.loads_bytes_strict(
        canonical.canonical_bytes(
            {
                **{k: v for k, v in manifest.items() if k != "digest"},
                "cells": [
                    {
                        "stratum": "s",
                        "requested": 1,
                        "eligible": 1,
                        "conflicts": 0,
                        "selected": 1,
                        "shortfall": 0,
                        "identities": [["other/repo", frozen.REVISION, "f.parquet", 1]],
                    }
                ],
            }
        )
    )
    foreign["digest"] = canonical.self_digest(foreign)
    with pytest.raises(text_costs.TextCostError, match="outside frozen repo"):
        text_costs.plan_text_costs(
            foreign,
            transport=footer.FakeFooterTransport({}),
            ledger=budgets.new_arm_t(),
        )


def test_text_costs_infeasible_file() -> None:
    table = _table(600, bulk_text_bytes=120000)
    images = {
        "dev-a.parquet": footer.FakeImage(_parquet_bytes([table])),
        "dev-b.parquet": _good_image((600,)),
    }
    manifest = _t_manifest()
    with pytest.raises(text_costs.TextCostsIncomplete) as caught:
        text_costs.plan_text_costs(
            manifest,
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )
    assert caught.value.failed_file == "dev-a.parquet"


def test_fixture_content_hash_stable() -> None:
    first = _parquet_bytes([_table(600), _table(600)])
    assert (
        hashlib.sha256(first).hexdigest()
        == hashlib.sha256(_parquet_bytes([_table(600), _table(600)])).hexdigest()
    )
