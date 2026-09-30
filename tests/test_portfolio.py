from __future__ import annotations

import pytest

from conftest import tool
from midas_hoard.errors import MidasError


def sid(meta):
    return meta["snapshot"]["id"]


def test_set_get_list_delete(svc, fetch):
    s = sid(fetch())
    res = tool(svc, "portfolio_set", name="main", holdings=[{"symbol": "fake.up", "quantity": 10, "snapshot_id": s, "currency": "USD"}], currency="USD")
    assert res["count"] == 1 and res["replaced"] is False
    assert tool(svc, "portfolio_analyze")["portfolios"][0]["name"] == "main"
    with pytest.raises(MidasError) as e:
        tool(svc, "portfolio_set", name="main", action="delete")
    assert e.value.code == "confirm_required"
    tool(svc, "portfolio_set", name="main", action="delete", confirm=True)
    assert tool(svc, "portfolio_analyze")["portfolios"] == []


def test_csv_holdings(svc, fetch):
    s1, s2 = sid(fetch("fake.up")), sid(fetch("fake.down"))
    csv = f"symbol,quantity,snapshot_id,currency\nfake.up,5,{s1},USD\nfake.down,20,{s2},USD\n"
    res = tool(svc, "portfolio_set", name="csv", csv_text=csv, currency="USD")
    assert res["count"] == 2


def test_analysis_single_currency(svc, fetch):
    s1, s2 = sid(fetch("fake.up")), sid(fetch("fake.down"))
    tool(svc, "portfolio_set", name="p", currency="USD", holdings=[
        {"symbol": "fake.up", "quantity": 10, "snapshot_id": s1, "currency": "USD"},
        {"symbol": "fake.down", "quantity": 100, "snapshot_id": s2, "currency": "USD"}])
    a = tool(svc, "portfolio_analyze", name="p")
    total = sum(h["value_base"] for h in a["holdings"]) if "value_base" in a["holdings"][0] else a["total_value"]
    assert abs(total - a["total_value"]) < 1e-6
    weights = [h["weight"] for h in a["holdings"]]
    assert abs(sum(weights) - 1.0) < 1e-6
    assert a["valuation_date"] == "2024-12-31"


def test_valuation_date_uses_prices_on_or_before(svc, fetch):
    s = sid(fetch())
    tool(svc, "portfolio_set", name="p", currency="USD", holdings=[{"symbol": "fake.up", "quantity": 1, "snapshot_id": s, "currency": "USD"}])
    a = tool(svc, "portfolio_analyze", name="p", date="2020-06-30")
    assert a["valuation_date"] <= "2020-06-30"
    assert a["holdings"][0]["price_date"] <= "2020-06-30"


def test_foreign_currency_needs_fx_and_records_it(svc, fetch):
    usd = sid(fetch("fake.up"))
    eur = sid(tool(svc, "market_fetch", provider="fake", symbol="fake.down", currency="EUR"))
    fx = sid(fetch("fakefx.eurusd"))
    tool(svc, "portfolio_set", name="mix", currency="EUR", holdings=[
        {"symbol": "fake.up", "quantity": 10, "snapshot_id": usd, "currency": "USD"},
        {"symbol": "fake.down", "quantity": 10, "snapshot_id": eur, "currency": "EUR"}])
    a = tool(svc, "portfolio_analyze", name="mix", fx={"USD": fx})
    conv = [h for h in a["holdings"] if h["currency"] == "USD"][0]
    assert conv["price_base"] != conv["price_native"]
    conv_rec = a["conversions"][0]
    assert conv_rec["fx_snapshot"] == fx and conv_rec["auto"] is False and conv_rec["holding"] == "fake.up"


def test_missing_fx_is_refused_or_auto_selected_and_marked(svc, fetch):
    usd = sid(fetch("fake.up"))
    tool(svc, "portfolio_set", name="mix", currency="EUR", holdings=[{"symbol": "fake.up", "quantity": 10, "snapshot_id": usd, "currency": "USD"}])
    try:
        a = tool(svc, "portfolio_analyze", name="mix")
        assert a["conversions"][0]["auto"] is True and any("auto-selected" in w for w in a["warnings"])
    except MidasError as e:
        assert e.code in ("incompatible_series", "no_data", "not_found")


def test_unknown_portfolio(svc):
    with pytest.raises(MidasError) as e:
        tool(svc, "portfolio_analyze", name="ghost")
    assert e.value.code == "not_found"


def test_fx_accepts_a_snapshot_id_string_and_says_what_was_applied(svc, fetch):
    usd = sid(fetch("fake.up"))
    fx = sid(fetch("fakefx.eurusd"))
    tool(svc, "portfolio_set", name="mixs", currency="EUR", holdings=[{"symbol": "fake.up", "quantity": 10, "snapshot_id": usd, "currency": "USD"}])
    a = tool(svc, "portfolio_analyze", name="mixs", fx=fx)
    assert a["conversions"][0]["fx_snapshot"] == fx and a["conversions"][0]["auto"] is False
    assert any(fx in w and "applied to convert USD holdings to EUR" in w for w in a["warnings"])
    tool(svc, "portfolio_set", name="gbp", currency="GBP", holdings=[{"symbol": "fake.up", "quantity": 1, "snapshot_id": usd, "currency": "USD"}])
    with pytest.raises(MidasError) as e:
        tool(svc, "portfolio_analyze", name="gbp", fx=fx)
    assert e.value.code == "incompatible_series"


def test_market_series_fx_string_records_the_conversion(svc, fetch):
    usd = sid(fetch("fake.up"))
    fx = sid(fetch("fakefx.eurusd"))
    out = tool(svc, "market_series", snapshot_id=usd, convert_to="EUR", fx=fx)
    assert json_has(out, "fx_snapshot", fx)


def json_has(obj, key, value):
    if isinstance(obj, dict):
        return obj.get(key) == value or any(json_has(v, key, value) for v in obj.values())
    if isinstance(obj, list):
        return any(json_has(v, key, value) for v in obj)
    return False
