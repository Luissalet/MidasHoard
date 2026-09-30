"""Yahoo chart endpoint, the Stooq browser check and the keyed providers, all against mocked HTTP."""

from __future__ import annotations

import json

import httpx
import pytest

from midas_hoard.agent_tools import call_tool
from midas_hoard.errors import MidasError
from test_providers import make_services

DAY = 86400
T = 1704205800  # 2024-01-02 14:30 UTC (New York session open)


def chart(n=6, nulls=(2,), currency="USD", itype="EQUITY", tz="America/New_York", splits=True):
    stamps = [T + i * DAY for i in range(n)]
    col = lambda base: [None if i in nulls else base + i for i in range(n)]  # noqa: E731
    adj = [None if i in nulls else 9.0 + i for i in range(n)]
    events = {"dividends": {str(T + DAY): {"amount": 0.24, "date": T + DAY}}}
    if splits:
        events["splits"] = {str(T + 3 * DAY): {"date": T + 3 * DAY, "numerator": 4, "denominator": 1, "splitRatio": "4:1"}}
    return {"chart": {"result": [{"meta": {"currency": currency, "symbol": "AAPL", "instrumentType": itype, "exchangeTimezoneName": tz,
                                           "gmtoffset": -18000, "longName": "Apple Inc."},
                                  "timestamp": stamps, "events": events,
                                  "indicators": {"quote": [{"open": col(10), "high": col(12), "low": col(9), "close": col(11), "volume": col(1000)}],
                                                 "adjclose": [{"adjclose": adj}]}}], "error": None}}


def test_stooq_browser_check_is_provider_unavailable(tmp_path):
    page = "<!DOCTYPE html><html><body>This site requires JavaScript to verify your browser</body></html>"
    svc = make_services(tmp_path, lambda r: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "stooq", "symbol": "aapl.us"})
    assert e.value.code == "provider_unavailable"
    assert "browser check" in e.value.hint and "yahoo" in e.value.hint
    # an HTML body with a plain content type is caught by its first bytes
    svc2 = make_services(tmp_path / "b", lambda r: httpx.Response(200, text="<html><head></head></html>"))
    with pytest.raises(MidasError) as e2:
        call_tool(svc2, "market_fetch", {"provider": "stooq", "symbol": "eurusd"})
    assert e2.value.code == "provider_unavailable"


def test_stooq_described_as_blocked_and_yahoo_first(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(500))
    provs = call_tool(svc, "market_providers", {})["providers"]
    by = {p["id"]: p for p in provs}
    assert by["stooq"]["status"] == "blocked" and "browser check" in by["stooq"]["reason"]
    assert provs[0]["id"] == "yahoo" and by["yahoo"]["unofficial"] and by["yahoo"]["status"] == "ok"
    assert by["alphavantage"]["status"] == "needs_key" and by["tiingo"]["status"] == "needs_key"
    seeds = svc.db.query("SELECT provider, COUNT(*) AS n FROM symbols WHERE seed = 1 GROUP BY provider")
    counts = {r["provider"]: r["n"] for r in seeds}
    assert counts["yahoo"] >= 20 and "stooq" not in counts


def test_yahoo_happy_path_nulls_splits_and_local_dates(tmp_path):
    seen = {}

    def handler(request):
        seen["url"], seen["ua"] = str(request.url), request.headers["user-agent"]
        return httpx.Response(200, json=chart())

    svc = make_services(tmp_path, handler)
    res = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "AAPL", "start": "2024-01-01"})
    assert "query1.finance.yahoo.com/v8/finance/chart/AAPL" in seen["url"] and "events=div%2Csplits" in seen["url"]
    assert seen["ua"].startswith("Mozilla/5.0")
    snap = res["snapshot"]
    assert snap["rows"] == 5  # the null row is skipped
    assert snap["currency"] == "USD" and snap["adjusted"] is True
    loaded = svc.store.load(snap["id"])
    assert loaded.df.index[0].strftime("%Y-%m-%d") == "2024-01-02"  # 09:30 local, not the UTC day
    assert loaded.df["close"].iloc[0] == 9.0  # adjusted close
    assert loaded.df["open"].iloc[0] == pytest.approx(10 * 9.0 / 11.0)  # OHLC scaled by the same factor
    assert "1 splits" in res["note"] and "1 dividends" in res["note"]


def test_yahoo_index_fx_and_pence(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, json=chart(nulls=(), itype="INDEX", splits=False)))
    meta = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "^GSPC"})["snapshot"]
    assert meta["currency"] == "USD" and meta["unit"] == "index points"
    svc2 = make_services(tmp_path / "p", lambda r: httpx.Response(200, json=chart(nulls=(), currency="GBp", splits=False)))
    meta2 = call_tool(svc2, "market_fetch", {"provider": "yahoo", "symbol": "VOD.L"})["snapshot"]
    assert meta2["currency"] == "GBX"


def test_yahoo_falls_back_to_query2(tmp_path):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(503) if request.url.host.startswith("query1") else httpx.Response(200, json=chart(nulls=()))

    svc = make_services(tmp_path, handler)
    assert call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "AAPL"})["snapshot"]["rows"] == 6
    assert hosts[-1].startswith("query2")


def test_yahoo_errors(tmp_path):
    err = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}
    svc = make_services(tmp_path, lambda r: httpx.Response(404, json=err))
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "ZZZZ"})
    assert e.value.code == "symbol_not_found"
    svc2 = make_services(tmp_path / "r", lambda r: httpx.Response(429, headers={"retry-after": "30"}))
    with pytest.raises(MidasError) as e2:
        call_tool(svc2, "market_fetch", {"provider": "yahoo", "symbol": "AAPL"})
    assert e2.value.code == "rate_limited"


def test_yahoo_search(tmp_path):
    body = {"quotes": [{"symbol": "SAN.MC", "longname": "Banco Santander, S.A.", "quoteType": "EQUITY", "exchDisp": "Madrid"},
                       {"symbol": "EURUSD=X", "shortname": "EUR/USD", "quoteType": "CURRENCY"},
                       {"symbol": "XYZ", "quoteType": "OPTION"}]}
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=body)

    svc = make_services(tmp_path, handler)
    res = call_tool(svc, "market_search", {"query": "santander", "provider": "yahoo", "remote": True})
    assert "v1/finance/search" in seen["url"] and "quotesCount=" in seen["url"]
    hits = {r["symbol"]: r for r in res["results"]}
    assert hits["SAN.MC"]["kind"] == "equity" and hits["EURUSD=X"]["kind"] == "fx" and "XYZ" not in hits


AV = {"Meta Data": {}, "Time Series (Daily)": {f"2024-01-0{d}": {"1. open": "10", "2. high": "12", "3. low": "9", "4. close": str(10 + d), "5. volume": "500"}
                                               for d in range(1, 6)}}
TIINGO = [{"date": f"2024-01-0{d}T00:00:00.000Z", "close": 100 + d, "adjClose": 50 + d, "adjOpen": 49 + d, "adjHigh": 52 + d, "adjLow": 48 + d,
           "adjVolume": 1000, "volume": 500, "divCash": 0.0, "splitFactor": 2.0 if d == 3 else 1.0} for d in range(1, 6)]


def test_alphavantage_with_and_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDAS_ALPHAVANTAGE_KEY", raising=False)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=AV)

    svc = make_services(tmp_path, handler)
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "alphavantage", "symbol": "IBM"})
    assert e.value.code == "provider_unavailable" and "key" in e.value.message and not calls
    monkeypatch.setenv("MIDAS_ALPHAVANTAGE_KEY", "DEMOKEY123456")
    meta = call_tool(svc, "market_fetch", {"provider": "alphavantage", "symbol": "ibm", "currency": "USD"})["snapshot"]
    assert meta["rows"] == 5 and meta["adjusted"] is False and "function=TIME_SERIES_DAILY" in calls[0] and "apikey=DEMOKEY123456" in calls[0]
    by = {p["id"]: p for p in call_tool(svc, "market_providers", {})["providers"]}
    assert by["alphavantage"]["status"] == "ok" and by["alphavantage"]["configured"] is True


@pytest.mark.parametrize("body,code", [({"Error Message": "Invalid API call"}, "symbol_not_found"),
                                       ({"Note": "Thank you for using Alpha Vantage! call frequency"}, "rate_limited")])
def test_alphavantage_errors(tmp_path, monkeypatch, body, code):
    monkeypatch.setenv("MIDAS_ALPHAVANTAGE_KEY", "DEMOKEY123456")
    svc = make_services(tmp_path, lambda r: httpx.Response(200, json=body))
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "alphavantage", "symbol": "IBM"})
    assert e.value.code == code
    assert "DEMOKEY123456" not in json.dumps(e.value.to_dict())


def test_tiingo_with_and_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDAS_TIINGO_KEY", raising=False)
    seen = {}

    def handler(request):
        seen["auth"], seen["url"] = request.headers.get("authorization"), str(request.url)
        return httpx.Response(200, json=TIINGO)

    svc = make_services(tmp_path, handler)
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "tiingo", "symbol": "aapl"})
    assert e.value.code == "provider_unavailable" and not seen
    svc.update_settings({"keys": {"tiingo": "tok_abcdef123456"}})
    res = call_tool(svc, "market_fetch", {"provider": "tiingo", "symbol": "AAPL"})
    assert seen["auth"] == "Token tok_abcdef123456" and "/tiingo/daily/aapl/prices" in seen["url"]
    assert res["snapshot"]["rows"] == 5 and res["snapshot"]["adjusted"] is True and res["snapshot"]["currency"] == "USD"
    assert svc.store.load(res["snapshot"]["id"]).df["close"].iloc[0] == 51.0


def test_tiingo_not_found_and_bad_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDAS_TIINGO_KEY", "tok_abcdef123456")
    svc = make_services(tmp_path, lambda r: httpx.Response(404, json={"detail": "Error: Ticker 'zzz' not found"}))
    with pytest.raises(MidasError) as e:
        call_tool(svc, "market_fetch", {"provider": "tiingo", "symbol": "zzz"})
    assert e.value.code == "symbol_not_found"
    svc2 = make_services(tmp_path / "x", lambda r: httpx.Response(401, json={"detail": "Invalid token."}))
    with pytest.raises(MidasError) as e2:
        call_tool(svc2, "market_fetch", {"provider": "tiingo", "symbol": "aapl"})
    assert e2.value.code == "provider_unavailable"


def test_secrets_are_write_only(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDAS_TIINGO_KEY", raising=False)
    monkeypatch.delenv("MIDAS_ALPHAVANTAGE_KEY", raising=False)
    svc = make_services(tmp_path, lambda r: httpx.Response(500))
    out = svc.update_settings({"keys": {"alphavantage": "SECRETVALUE9876"}})
    assert out["keys"]["alphavantage"] == {"configured": True, "last4": "9876", "source": "settings"}
    assert out["keys"]["tiingo"]["configured"] is False
    dumped = json.dumps([svc.get_settings(), svc.status(), call_tool(svc, "market_providers", {}), call_tool(svc, "midas_status", {})])
    assert "SECRETVALUE" not in dumped
    with pytest.raises(MidasError):
        svc.update_settings({"keys": {"fred": "x"}})
    assert svc.update_settings({"keys": {"alphavantage": ""}})["keys"]["alphavantage"]["configured"] is False


def test_yahoo_index_keeps_its_denomination_and_compares_with_a_stock(tmp_path):
    def handler(request):
        if "GSPC" in str(request.url):
            return httpx.Response(200, json=chart(nulls=(), itype="INDEX", splits=False))
        return httpx.Response(200, json=chart(nulls=(), splits=False))

    svc = make_services(tmp_path, handler)
    aapl = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "AAPL"})["snapshot"]["id"]
    spx = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "^GSPC"})["snapshot"]
    assert spx["currency"] == "USD" and spx["unit"] == "index points"
    out = call_tool(svc, "market_compare", {"snapshot_ids": [aapl, spx["id"]]})
    assert "currencies differ" not in json.dumps(out) and out.get("warnings") is not None


def test_thesis_assets_accept_snapshot_ids_and_feed_the_committee(tmp_path):
    svc = make_services(tmp_path, lambda r: httpx.Response(200, json=chart(nulls=(), splits=False)))
    a = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "AAPL"})["snapshot"]
    b = call_tool(svc, "market_fetch", {"provider": "yahoo", "symbol": "^GSPC"})["snapshot"]
    t = call_tool(svc, "thesis_create", {"title": "Trend", "claim": "AAPL keeps rising", "rival": "a rival view that is long enough", "as_of": "2024-01-03",
                                         "assets": [a["id"], {"snapshot_id": b["id"]}, {"symbol": "AAPL", "provider": "yahoo"}]})
    assets = call_tool(svc, "thesis_get", {"id": t["id"]})["assets"]
    assert assets[0] == {"snapshot_id": a["id"], "symbol": "AAPL", "provider": "yahoo"}
    assert assets[1]["symbol"] == "^GSPC" and assets[2] == {"symbol": "AAPL", "provider": "yahoo"}
    upd = call_tool(svc, "thesis_update", {"id": t["id"], "assets": [b["id"]]})
    assert call_tool(svc, "thesis_get", {"id": t["id"]})["assets"][0]["symbol"] == "^GSPC"
    res = call_tool(svc, "committee_run", {"thesis_id": t["id"], "use_model": False})
    assert res["material"]["assets"] and not res["deterministic"].get("assets_missing_data")
