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
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        if headers is None:
            total = total if total is not None else len(body)
            headers = {
                "Content-Range": f"bytes 0-{len(body) - 1}/{total}",
                "Content-Length": str(len(body)),
                "ETag": etag,
            }
            if etag is None:
                del headers["ETag"]
        self.headers = headers
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


class _ChainOpener:
    """Stub opener that follows 3xx chains through the REAL hop handler.

    Routes map URL -> list of responder callables; each responder takes the
    request and returns (status, headers, body) or raises. Redirects are
    followed exactly like urllib: handler.redirect_request, then open the
    new URL. Every opened URL is logged.
    """

    def __init__(
        self,
        handler: footer.HopCountingRedirectHandler,
        routes: dict[str, list[Any]],
    ) -> None:
        self.handler = handler
        self.routes = {url: list(actions) for url, actions in routes.items()}
        self.log: list[str] = []

    def open(self, request: Any, timeout: float | None = None) -> Any:
        current = request
        while True:
            url = current.full_url
            self.log.append(url)
            actions = self.routes.get(url)
            assert actions, f"unexpected URL opened: {url}"
            action = actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            status, headers, body = action(current)
            if status in (301, 302, 303, 307, 308) and "Location" in headers:
                fp = _StubResponse(b"", headers={})
                current = self.handler.redirect_request(
                    current, fp, status, "msg", headers, headers["Location"]
                )
                continue
            return _StubResponse(body, status=status, headers=headers, url=url)


def _chain_transport(
    routes: dict[str, list[Any]],
) -> tuple[footer.LiveFooterTransport, _ChainOpener]:
    from xlm.data.sources.transport import TransportBudget as _Budget

    budget = _Budget(max_bytes=33554432, max_requests=80)
    holder: dict[str, _ChainOpener] = {}

    def factory(handler: Any) -> _ChainOpener:
        holder["opener"] = _ChainOpener(handler, routes)
        return holder["opener"]

    transport = footer.LiveFooterTransport(budget, sleep=lambda _: None, opener_factory=factory)
    return transport, holder["opener"]


def _respond_final(body: bytes, total: int) -> Any:
    def respond(request: Any) -> tuple[int, dict[str, str], bytes]:
        text = request.get_header("Range") or ""
        start, end = text.replace("bytes=", "").split("-")
        return (
            206,
            {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(len(body)),
                "ETag": "chain-etag",
            },
            body,
        )

    return respond


def _respond_redirect(url: str, code: int = 302) -> Any:
    def respond(request: Any) -> tuple[int, dict[str, str], bytes]:
        return (code, {"Location": url}, b"")

    return respond


def _chain_urls(base: str, n: int) -> list[str]:
    return [f"{base}/r{i}?sig=SECRET{i}" for i in range(1, n + 1)]


def _chain_routes(initial: str, hops: list[str], body: bytes, total: int) -> dict[str, list[Any]]:
    routes: dict[str, list[Any]] = {}
    previous = initial
    for hop in hops:
        routes.setdefault(previous, []).append(_respond_redirect(hop))
        previous = hop
    routes.setdefault(previous, []).append(_respond_final(body, total))
    return routes


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


def test_text_costs_failed_unit_preserved_numbers_only() -> None:
    # The refusal decision is unchanged, but the failed file's computed
    # cost evidence (wanted rows, groups, chunk sizes, uppers) survives
    # for audit without re-fetching footers. No text content survives.
    bodies = _distinct_payloads("body", 600, 120000)
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
    unit = caught.value.failed_unit
    assert unit is not None
    assert unit["file"] == "dev-a.parquet"
    assert unit["wanted_rows"] == [10, 500]
    assert unit["feasible"] is False
    assert any("transfer upper" in reason for reason in unit["reasons"])
    costs = unit["text_costs"]
    assert costs["chunk_count"] >= 1
    assert unit["transfer_upper_bytes"] == (
        costs["text_compressed_bytes"] + costs["chunk_count"] * 4194304
    )
    assert unit["scan_rows_upper"] <= 16384
    import json as _json

    dumped = _json.dumps(unit)
    assert bodies[0] not in dumped
    assert bodies[599] not in dumped
    assert "body" not in dumped.replace('"text_costs"', "").replace('"text_leaf"', "")


def _manifest_for(wanted: dict[str, list[int]]) -> dict[str, Any]:
    identities = [
        [frozen.REPOSITORY, frozen.REVISION, name, row]
        for name, rows in sorted(wanted.items())
        for row in sorted(rows)
    ]
    manifest: dict[str, Any] = {
        "kind": "essential_web_evidence_v2_text_selection",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "total_selected": len(identities),
        "cells": [
            {
                "stratum": "B_science_census",
                "requested": 29,
                "eligible": 29,
                "conflicts": 0,
                "selected": len(identities),
                "shortfall": 0,
                "identities": identities,
            }
        ],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    return manifest


def test_collection_continues_past_infeasible_file() -> None:
    table = _table(600, bulk_text_bytes=120000)
    images = {
        "dev-a.parquet": footer.FakeImage(_parquet_bytes([table])),
        "dev-b.parquet": _good_image((600,)),
    }
    manifest = _manifest_for({"dev-a.parquet": [10, 500], "dev-b.parquet": [5]})
    with pytest.raises(text_costs.TextCostsIncomplete) as caught:
        text_costs.plan_text_costs(
            manifest,
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )
    exc = caught.value
    assert exc.stopped_early is False
    assert [u["file"] for u in exc.units] == ["dev-a.parquet", "dev-b.parquet"]
    assert exc.units[0]["feasible"] is False
    assert exc.units[1]["feasible"] is True
    assert exc.infeasible == [{"file": "dev-a.parquet", "reasons": exc.units[0]["reasons"]}]
    assert exc.failed_file == "dev-a.parquet"
    assert exc.failed_unit == exc.units[0]


def test_collection_records_multiple_infeasible_files() -> None:
    table = _table(600, bulk_text_bytes=120000)
    images = {
        "dev-a.parquet": footer.FakeImage(_parquet_bytes([table])),
        "dev-b.parquet": _good_image((600,)),
        "dev-c.parquet": footer.FakeImage(_parquet_bytes([table])),
    }
    manifest = _manifest_for({"dev-a.parquet": [10], "dev-b.parquet": [5], "dev-c.parquet": [7]})
    with pytest.raises(text_costs.TextCostsIncomplete) as caught:
        text_costs.plan_text_costs(
            manifest,
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )
    assert [u["file"] for u in caught.value.units] == [
        "dev-a.parquet",
        "dev-b.parquet",
        "dev-c.parquet",
    ]
    assert [i["file"] for i in caught.value.infeasible] == [
        "dev-a.parquet",
        "dev-c.parquet",
    ]


def test_integrity_failure_stops_collection_immediately() -> None:
    images = {
        "dev-a.parquet": _good_image((600,)),
        "dev-c.parquet": _good_image((600,)),
    }
    manifest = _manifest_for({"dev-a.parquet": [10], "dev-b.parquet": [5], "dev-c.parquet": [7]})
    transport = footer.FakeFooterTransport(images)
    with pytest.raises(text_costs.TextCostsIncomplete) as caught:
        text_costs.plan_text_costs(manifest, transport=transport, ledger=budgets.new_arm_t())
    exc = caught.value
    assert exc.stopped_early is True
    assert exc.failed_file == "dev-b.parquet"
    assert [u["file"] for u in exc.units] == ["dev-a.parquet"]
    assert exc.infeasible == []
    assert all(call[0] != "dev-c.parquet" for call in transport.calls)


def test_budget_exhaustion_stops_collection_immediately() -> None:
    images = {
        "dev-a.parquet": _good_image((600,)),
        "dev-b.parquet": _good_image((600,)),
    }
    manifest = _manifest_for({"dev-a.parquet": [10], "dev-b.parquet": [5]})
    ledger = budgets.new_arm_t()
    for _ in range(100):
        ledger.charge_file_request("dev-a.parquet", kind="footer")
    transport = footer.FakeFooterTransport(images)
    with pytest.raises(text_costs.TextCostsIncomplete) as caught:
        text_costs.plan_text_costs(manifest, transport=transport, ledger=ledger)
    assert caught.value.stopped_early is True
    assert [u["file"] for u in caught.value.units] == []


def test_incomplete_aggregate_sums_fits_formulas() -> None:
    table = _table(600, bulk_text_bytes=120000)
    images = {
        "dev-a.parquet": footer.FakeImage(_parquet_bytes([table])),
        "dev-b.parquet": _good_image((600,)),
    }
    manifest = _manifest_for({"dev-a.parquet": [10, 500], "dev-b.parquet": [5]})
    try:
        text_costs.plan_text_costs(
            manifest,
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )
        raise AssertionError("expected TextCostsIncomplete")
    except text_costs.TextCostsIncomplete as exc:
        aggregate = text_costs.build_incomplete_aggregate(
            exc, manifest, command="plan-text-costs live", exit_status=1
        )
    assert aggregate["status"] == "INCOMPLETE"
    assert aggregate["receipt_schema_version"] == 2
    assert aggregate["files_planned"] == 2
    assert aggregate["files_feasible"] == 1
    assert aggregate["files_infeasible"] == 1
    units = {u["file"]: u for u in aggregate["units"]}
    assert aggregate["aggregate"]["transfer_upper_sum_bytes"] == sum(
        u["transfer_upper_bytes"] for u in units.values()
    )
    assert aggregate["aggregate"]["transfer_upper_max_bytes"] == max(
        u["transfer_upper_bytes"] for u in units.values()
    )
    assert aggregate["aggregate"]["decompressed_upper_sum_bytes"] == sum(
        u["decompressed_upper_bytes"] for u in units.values()
    )
    assert aggregate["aggregate"]["requests_upper_future_total"] == sum(
        u["requests_upper"] for u in units.values()
    )
    assert aggregate["aggregate"]["scan_upper_total_rows"] == sum(
        u["scan_rows_upper"] for u in units.values()
    )
    assert aggregate["formulas"]["transfer_upper_bytes"] == (
        "sum(text compressed chunk bytes over wanted groups) + chunk_count * 4194304"
    )
    assert aggregate["aggregate"]["fits"]["transfer_arm"]["cap"] == 134217728
    assert aggregate["aggregate"]["fits"]["transfer_arm"]["fits"] is True
    assert units["dev-a.parquet"]["fits"]["transfer"]["fits"] is False
    assert units["dev-b.parquet"]["fits"]["transfer"]["fits"] is True
    group = units["dev-b.parquet"]["text_costs"]["groups"][0]
    assert group["selected_count"] == 1
    assert group["selected_min"] == group["selected_max"] == 5
    assert isinstance(group["text_chunks"][0]["offset"], int)
    assert isinstance(group["dictionary_required"], bool)
    assert "not exposed by the current stack" in group["page_index"]
    assert aggregate["aggregate"]["refusal_reasons"] == sorted(units["dev-a.parquet"]["reasons"])
    sealed = receipts.seal(aggregate)
    assert sealed["digest"] == canonical.self_digest(
        {k: v for k, v in sealed.items() if k != "digest"}
    )


def test_collection_deterministic_and_caps_frozen() -> None:
    images = {
        "dev-a.parquet": _good_image((600,)),
        "dev-b.parquet": _good_image((600,)),
    }
    manifest = _manifest_for({"dev-a.parquet": [10], "dev-b.parquet": [5]})

    def run() -> dict[str, Any]:
        return text_costs.plan_text_costs(
            manifest,
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )

    first, second = run(), run()
    assert first["status"] == "COMPLETE"
    assert canonical.digest(first) == canonical.digest(second)
    assert frozen.ARM_T_LIMITS["transfer_bytes_per_file_max"] == 16777216
    assert frozen.ARM_T_LIMITS["data_bytes_per_file_max"] == 14680064
    assert frozen.ARM_T_LIMITS["requests_arm_max"] == 800
    assert frozen.ARM_T_LIMITS["decompressed_bytes_arm_max"] == 536870912


def test_text_costs_failed_unit_in_incomplete_receipt(tmp_path: Path) -> None:
    table = _table(600, bulk_text_bytes=120000)
    images = {
        "dev-a.parquet": footer.FakeImage(_parquet_bytes([table])),
        "dev-b.parquet": _good_image((600,)),
    }
    try:
        text_costs.plan_text_costs(
            _t_manifest(),
            transport=footer.FakeFooterTransport(images),
            ledger=budgets.new_arm_t(),
        )
        raise AssertionError("expected TextCostsIncomplete")
    except text_costs.TextCostsIncomplete as exc:
        receipt = receipts.seal(
            {
                "kind": "essential_web_evidence_v2_text_costs_incomplete",
                "protocol_version": frozen.PROTOCOL_VERSION,
                "freeze_digest": frozen.FREEZE_DIGEST,
                "completed_units": exc.units,
                "failed_file": exc.failed_file,
                "failed_unit": exc.failed_unit,
                "reason": exc.reason,
                "budget": exc.budget,
                "command": "plan-text-costs live",
                "exit_status": 1,
                "status": "INCOMPLETE",
            }
        )
    assert receipt["failed_unit"]["file"] == "dev-a.parquet"
    assert receipt["status"] == "INCOMPLETE"
    target = tmp_path / "text_cost_evidence.incomplete.json"
    receipts.publish_manifest(target, receipt)
    assert not (tmp_path / "text_cost_evidence.json").exists()


def test_fixture_content_hash_stable() -> None:
    first = _parquet_bytes([_table(600), _table(600)])
    assert (
        hashlib.sha256(first).hexdigest()
        == hashlib.sha256(_parquet_bytes([_table(600), _table(600)])).hexdigest()
    )


# --------------------------------------------------------------------------
# Redirect accounting: per-resolution-chain semantics (protocol <=3 hops).
# --------------------------------------------------------------------------


def _chain_base() -> str:
    return "https://cas-bridge.xethub.hf.co/x"


@pytest.mark.parametrize("hops", [0, 1, 2, 3])
def test_redirect_chains_up_to_3_accepted(hops: int) -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    chain = _chain_urls(_chain_base(), hops)
    transport, opener = _chain_transport(_chain_routes(initial, chain, b"PAR1", 100))
    evidence = transport.fetch_range(WINNERS[0], 0, 3)
    assert evidence.body == b"PAR1"
    assert evidence.redirect_hops == hops
    assert [entry["hop"] for entry in evidence.redirect_chain] == list(range(1, hops + 1))
    assert opener.log == [initial, *chain]


def test_redirect_4_refused_before_r4() -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    chain = _chain_urls(_chain_base(), 4)
    transport, opener = _chain_transport(_chain_routes(initial, chain, b"PAR1", 100))
    with pytest.raises(footer.FooterError, match="hop 4 exceeds"):
        transport.fetch_range(WINNERS[0], 0, 3)
    assert opener.log == [initial, *chain[:3]]
    assert chain[3] not in opener.log


def test_refusal_carries_reconstructable_chain() -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    chain = _chain_urls(_chain_base(), 4)
    transport, _ = _chain_transport(_chain_routes(initial, chain, b"PAR1", 100))
    try:
        transport.fetch_range(WINNERS[0], 0, 3)
        raise AssertionError("expected hop-4 refusal")
    except footer.FooterError as exc:
        diagnostics = exc.diagnostics
    assert diagnostics["transitions"] == 4
    assert diagnostics["attempt"] == 0
    entries = diagnostics["redirect_chain"]
    assert len(entries) == 4
    assert [e["status"] for e in entries] == [302, 302, 302, 302]
    assert {e["dest_host"] for e in entries} == {"cas-bridge.xethub.hf.co"}
    assert entries[0]["source_host"] == "huggingface.co"
    assert all(e["allowlist_ok"] for e in entries)
    assert entries[-1]["refused"] == "redirect hop 4 exceeds the 3-hop ceiling"
    import json as _json

    dumped = _json.dumps(diagnostics)
    assert "SECRET" not in dumped
    assert "sig" not in dumped


def test_retry_after_timeout_does_not_add_hops() -> None:
    transport, opener = _live([TimeoutError("t"), _StubResponse(b"PAR1", total=100)])
    evidence = transport.fetch_range(WINNERS[0], 0, 3)
    assert evidence.redirect_hops == 0
    assert evidence.redirect_chain == ()
    assert transport.budget.requests_made == 2
    assert len(opener.urls) == 2


def test_redirect_after_retry_counts_correctly() -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    first, second = _chain_urls(_chain_base(), 1)[0], _chain_base() + "/r1b?sig=SECRET9"
    routes = {
        initial: [_respond_redirect(first), _respond_redirect(second)],
        first: [TimeoutError("timeout on resolved target")],
        second: [_respond_final(b"PAR1", 100)],
    }
    transport, opener = _chain_transport(routes)
    evidence = transport.fetch_range(WINNERS[0], 0, 3)
    # Attempt 1 followed one redirect then timed out; the retry re-resolved
    # with a fresh hop budget and followed one redirect: counted, not doubled.
    assert evidence.redirect_hops == 1
    assert transport.budget.requests_made == 4
    assert opener.log == [initial, first, initial, second]


def test_chains_reset_per_logical_request_not_per_arm() -> None:
    # Mirrors the real failure shape: two sequential ranges each following
    # two redirects must both succeed; the pre-fix arm-scoped counter
    # refused the second range at "hop 4".
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[2])
    routes = _chain_routes(initial, _chain_urls(_chain_base(), 2), b"PAR1", 100)
    second = _chain_urls(_chain_base() + "/b", 2)
    for hop_routes in _chain_routes(initial, second, b"PAQ2", 100).items():
        routes.setdefault(hop_routes[0], []).extend(hop_routes[1])
    transport, _ = _chain_transport(routes)
    first = transport.fetch_range(WINNERS[2], 0, 3)
    second = transport.fetch_range(WINNERS[2], 0, 3)
    assert (first.redirect_hops, second.redirect_hops) == (2, 2)
    assert first.body == b"PAR1" and second.body == b"PAQ2"


def test_redirect_host_validated_before_following() -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    routes = {initial: [_respond_redirect("https://evil.example/z?sig=SECRETX")]}
    transport, opener = _chain_transport(routes)
    with pytest.raises(footer.FooterError):
        transport.fetch_range(WINNERS[0], 0, 3)
    assert opener.log == [initial]
    assert not any("evil.example" in url for url in opener.log[1:])


def test_evil_redirect_chain_marks_allowlist_failure() -> None:
    transport, _ = _live([])
    initial = transport.canonical_url(WINNERS[0])
    routes = {initial: [_respond_redirect("https://evil.example/z?sig=SECRETX")]}
    transport, _ = _chain_transport(routes)
    try:
        transport.fetch_range(WINNERS[0], 0, 3)
        raise AssertionError("expected allowlist refusal")
    except footer.FooterError as exc:
        entries = exc.diagnostics["redirect_chain"]
    assert len(entries) == 1
    assert entries[0]["allowlist_ok"] is False
    assert entries[0]["dest_host"] == "evil.example"
    assert entries[0]["dest_path"] == "/z"
    import json as _json

    assert "SECRETX" not in _json.dumps(entries)


def test_ledger_charges_redirects_as_footer_requests() -> None:
    images = _images(WINNERS)
    images[WINNERS[0]] = footer.FakeImage(images[WINNERS[0]].content, redirect_hops=2)
    transport = footer.FakeFooterTransport(images)
    ledger = budgets.new_arm_m()
    unit = footer.plan_one_file(WINNERS[0], transport=transport, ledger=ledger, winners=WINNERS)
    ranges = unit["footer_ranges"]
    assert ranges and all(r["hops"] == 2 for r in ranges)
    assert ledger.snapshot()["requests"] == 3 * len(ranges)
    assert ledger.snapshot()["kind_requests"]["footer"] == 3 * len(ranges)


def test_incomplete_receipt_embeds_redirect_diagnostics(tmp_path: Path) -> None:
    probe, _ = _live([])
    initial = probe.canonical_url(WINNERS[0])
    routes = _chain_routes(initial, _chain_urls(_chain_base(), 4), b"PAR1", 100)
    live, _ = _chain_transport(routes)
    images = _images(WINNERS)

    class _ChainedFake(footer.FakeFooterTransport):
        def fetch_range(self, source_file: str, start: int, end: int) -> Any:
            if source_file == WINNERS[0]:
                return live.fetch_range(source_file, start, end)
            return super().fetch_range(source_file, start, end)

    fake = _ChainedFake(images)
    ledger = budgets.new_arm_m()
    try:
        footer.plan_arm_m(fake, ledger, WINNERS)
        raise AssertionError("expected ArmIncomplete")
    except footer.ArmIncomplete as exc:
        receipt = receipts.seal(footer.incomplete_receipt(exc, command="plan-footers"))
    assert receipt["failed_file"] == WINNERS[0]
    assert receipt["completed_units"] == []
    diagnostics = receipt["redirect_diagnostics"]
    assert diagnostics["transitions"] == 4
    assert len(diagnostics["redirect_chain"]) == 4
    assert diagnostics["file"] == WINNERS[0]
    assert "resource" in diagnostics and "sig" not in diagnostics["resource"]
    target = tmp_path / "footer_evidence.incomplete.json"
    receipts.publish_manifest(target, receipt)
    assert not (tmp_path / "footer_evidence.json").exists()
    import json as _json

    assert "SECRET" not in target.read_text(encoding="utf-8")
    assert _json.loads(target.read_bytes())["digest"] == receipt["digest"]
