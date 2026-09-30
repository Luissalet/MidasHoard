from __future__ import annotations

import httpx
import pytest

from conftest import T0, make_config
from midas_hoard.agent_tools import call_tool
from midas_hoard.errors import MidasError
from midas_hoard.services import Services

STOOQ = "Date,Open,High,Low,Close,Volume\n" + "\n".join(f"2024-01-{d:02d},{10+d},{11+d},{9+d},{10.5+d},1000" for d in range(1, 11)) + "\n"
FRED = "observation_date,CPIAUCSL\n2023-01-01,300.0\n2023-02-01,301.0\n2023-03-01,.\n2023-04-01,303.5\n"
ECB = ("KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
       "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2024-01-02,1.10\nEXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2024-01-03,1.11\n"
       "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2024-01-04,\n")


def make_services(tmp_path, handler, **cfg):
    transport = httpx.MockTransport(handler)
    return Services(make_config(tmp_path, **cfg), link=None.__class__ and _NoLink(), http_transport=transport,
                    clock_fn=lambda: T0, sleep_fn=lambda _s: None)


class _NoLink:
    def chat(self, *a, **k):
        raise RuntimeError("offline")

    def status(self):
        return {}

    def close(self):
        pass


def test_stooq_fetch_and_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=STOOQ)

    svc = make_services(tmp_path, handler)
    try:
        a = call_tool(svc, "market_fetch", {"provider": "stooq", "symbol": "AAPL.US"})
        snap = a["snapshot"]
        assert snap["currency"] == "USD" and snap["rows"] == 10 and snap["adjusted"] is False
        b = call_tool(svc, "market_fetch", {"provider": "stooq", "symbol": "aapl.us"})
        assert b["snapshot"]["id"] == snap["id"]  # identical bytes reuse the snapshot
        assert len(calls) == 1  # second fetch hit the on-disk cache
    finally:
        svc.stop()


def test_stooq_no_data_is_symbol_not_found(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text="No data"))
    try:
        with pytest.raises(MidasError) as e:
            call_tool(svc, "market_fetch", {"provider": "stooq", "symbol": "zzzz.zz"})
        assert e.value.code == "symbol_not_found" and e.value.status == 404
    finally:
        svc.stop()


def test_fred_drops_missing_observations(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text=FRED))
    try:
        snap = call_tool(svc, "market_fetch", {"provider": "fred", "symbol": "cpiaucsl"})["snapshot"]
        assert snap["rows"] == 3 and snap["frequency"] == "M" and snap["symbol"].upper() == "CPIAUCSL"
    finally:
        svc.stop()


def test_ecb_fx_pair_metadata(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text=ECB))
    try:
        snap = call_tool(svc, "market_fetch", {"provider": "ecb", "symbol": "EXR/D.USD.EUR.SP00.A"})["snapshot"]
        assert snap["rows"] == 2 and snap["currency"] == "USD" and snap["unit"] == "USD per EUR"
    finally:
        svc.stop()


def test_ecb_symbol_needs_flow(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text=""))
    try:
        with pytest.raises(MidasError) as e:
            call_tool(svc, "market_fetch", {"provider": "ecb", "symbol": "USD"})
        assert e.value.code == "invalid_request"
    finally:
        svc.stop()


def test_coingecko_drops_partial_day(tmp_path):
    import json
    day = 86_400_000
    t0_ms = int(T0 * 1000) // day * day
    prices = [[t0_ms - (4 - i) * day, 100.0 + i] for i in range(5)]  # last point is "today" 00:00 UTC
    body = {"prices": prices, "total_volumes": [[p[0], 5.0] for p in prices]}
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text=json.dumps(body)))
    try:
        res = call_tool(svc, "market_fetch", {"provider": "coingecko", "symbol": "bitcoin:usd"})
        assert res["snapshot"]["rows"] == 4 and res["snapshot"]["currency"] == "USD"
        assert "partial current day" in res["note"]
    finally:
        svc.stop()


def test_rate_limit_maps_to_429(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(429, text="slow down", headers={"Retry-After": "0"}))
    try:
        with pytest.raises(MidasError) as e:
            call_tool(svc, "market_fetch", {"provider": "fred", "symbol": "UNRATE"})
        assert e.value.code == "rate_limited" and e.value.status == 429
    finally:
        svc.stop()


def test_server_error_falls_back_to_stale_cache(tmp_path):
    state = {"ok": True}

    def handler(r):
        return httpx.Response(200, text=FRED) if state["ok"] else httpx.Response(503, text="down")

    times = {"now": T0}
    transport = httpx.MockTransport(handler)
    svc = Services(make_config(tmp_path, cache_ttl_s=10), link=_NoLink(), http_transport=transport, clock_fn=lambda: times["now"], sleep_fn=lambda _s: None)
    try:
        call_tool(svc, "market_fetch", {"provider": "fred", "symbol": "CPIAUCSL"})
        times["now"] += 1000  # cache entry is now stale
        state["ok"] = False
        again = call_tool(svc, "market_fetch", {"provider": "fred", "symbol": "CPIAUCSL"})
        assert again["snapshot"]["rows"] == 3
    finally:
        svc.stop()


def test_server_error_without_cache_is_provider_unavailable(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(503, text="down"))
    try:
        with pytest.raises(MidasError) as e:
            call_tool(svc, "market_fetch", {"provider": "fred", "symbol": "CPIAUCSL"})
        assert e.value.code == "provider_unavailable" and e.value.status == 502
    finally:
        svc.stop()


def test_offline_mode_never_touches_the_network(tmp_path):
    def handler(r):
        raise AssertionError("network used")

    svc = make_services(tmp_path, handler, offline=True)
    try:
        with pytest.raises(MidasError) as e:
            call_tool(svc, "market_fetch", {"provider": "stooq", "symbol": "aapl.us"})
        assert e.value.code == "provider_unavailable"
        assert call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fake.up"})["snapshot"]["rows"] > 1000
    finally:
        svc.stop()


def test_unknown_provider(svc):
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "nope", "symbol": "x"})
    assert e.value.code in ("invalid_request", "not_found")


# ---------------------------------------------------------------- csv
CSV_ES = "Fecha;Cierre\n01/02/2024;1.234,50\n02/02/2024;1.240,00\n05/02/2024;1.251,25\n"


def test_csv_import_with_mapping_and_declared_currency(svc):
    snap = call_tool(svc, "market_fetch", {"provider": "csv", "symbol": "mi-fondo", "csv_text": CSV_ES, "currency": "EUR", "dayfirst": True,
                                           "delimiter": ";", "decimal": ",", "mapping": {"date": "Fecha", "close": "Cierre"}})["snapshot"]
    assert snap["rows"] == 3 and snap["currency"] == "EUR" and snap["actual_start"] == "2024-02-01"
    series = call_tool(svc, "market_series", {"snapshot_id": snap["id"], "limit": 5})
    assert series["points"][0]["value"] == 1234.5


def test_csv_requires_currency_or_unit(svc):
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "csv", "symbol": "x", "csv_text": "date,close\n2024-01-01,1\n2024-01-02,2\n"})
    assert e.value.code == "declaration_required"


def test_csv_rejects_non_numeric_close(svc):
    with pytest.raises((MidasError, ValueError)):
        call_tool(svc, "market_fetch", {"provider": "csv", "symbol": "x", "currency": "USD", "csv_text": "date,close\n2024-01-01,abc\n2024-01-02,def\n"})


# ---------------------------------------------------------------- fake
def test_fake_is_deterministic_and_presets_differ(svc):
    a = call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fake.up"})["snapshot"]
    b = call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fake.up"})["snapshot"]
    d = call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fake.down"})["snapshot"]
    assert a["id"] == b["id"] and a["sha256"] == b["sha256"] != d["sha256"]
    up = call_tool(svc, "market_series", {"snapshot_id": a["id"], "limit": 1000})
    assert up["summary"]["total_return"] > 0
    down = call_tool(svc, "market_series", {"snapshot_id": d["id"], "limit": 1000})
    assert down["summary"]["total_return"] < 0


def test_fake_range_and_fx_pair(svc):
    snap = call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fakefx.eurusd", "start": "2020-01-01", "end": "2020-12-31"})["snapshot"]
    assert snap["actual_start"] >= "2020-01-01" and snap["actual_end"] <= "2020-12-31"
    assert snap["unit"] == "USD per EUR"
