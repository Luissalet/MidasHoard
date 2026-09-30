from __future__ import annotations

import pytest

from conftest import tool
from midas_hoard.errors import MidasError
from midas_hoard.snapshots import canonical_csv, rows_sha256


def sid(meta):
    return meta["snapshot"]["id"]


def test_snapshot_has_hash_and_verifies_on_load(svc, fetch):
    meta = fetch()["snapshot"]
    assert len(meta["sha256"]) == 64 and meta["id"].startswith("snp_")
    path = svc.config.snapshots_dir / f"{meta['id']}.csv"
    assert path.is_file()
    svc.store.load(meta["id"])
    path.write_bytes(path.read_bytes().replace(b"2015", b"2016", 1))  # tamper
    with pytest.raises(MidasError) as e:
        svc.store.load(meta["id"])
    assert e.value.code == "integrity_error"


def test_canonical_csv_is_stable(svc, fetch):
    df = svc.store.load(sid(fetch())).df
    assert canonical_csv(df) == canonical_csv(df.copy())
    assert rows_sha256(df) == rows_sha256(df.copy())


def test_as_of_cut_hides_future_rows(svc, fetch):
    s = sid(fetch())
    full = svc.store.load(s)
    cut = svc.store.load(s, as_of="2020-06-30")
    assert cut.df.index.max() <= __import__("pandas").Timestamp("2020-06-30") < full.df.index.max()


def test_series_view_paginates(svc, fetch):
    s = sid(fetch())
    page1 = tool(svc, "market_series", snapshot_id=s, limit=50)
    assert len(page1["points"]) == 50 and page1["next_cursor"] is None and page1["first_cursor"] == page1["total"] - 50
    first = tool(svc, "market_series", snapshot_id=s, limit=50, cursor=0)
    assert first["points"][-1]["date"] < page1["points"][0]["date"] and first["next_cursor"] == 50
    second = tool(svc, "market_series", snapshot_id=s, limit=50, cursor=first["next_cursor"])
    assert second["points"][0]["date"] > first["points"][-1]["date"]


def test_series_transforms(svc, fetch):
    s = sid(fetch())
    r = tool(svc, "market_series", snapshot_id=s, limit=5, rebase=True, cursor=0)
    assert abs(r["points"][0]["rebased"] - 100.0) < 1e-6
    w = tool(svc, "market_series", snapshot_id=s, limit=500, resample="M")
    assert 100 < w["total"] < 130  # ~10 years of month ends
    dd = tool(svc, "market_series", snapshot_id=s, limit=5, drawdown=True)
    assert all(p["drawdown_pct"] <= 0 for p in dd["points"])
    v = tool(svc, "market_series", snapshot_id=s, limit=5, vol_window=20)
    assert v["points"]


def test_series_as_of_respected(svc, fetch):
    s = sid(fetch())
    r = tool(svc, "market_series", snapshot_id=s, as_of="2019-12-31", limit=1000)
    assert max(p["date"] for p in r["points"]) <= "2019-12-31"


def test_unknown_snapshot(svc):
    with pytest.raises(MidasError) as e:
        tool(svc, "market_series", snapshot_id="snp_missing")
    assert e.value.code == "not_found"


def test_compare_same_currency_ok(svc, fetch):
    a, b = sid(fetch("fake.up")), sid(fetch("fake.down"))
    r = tool(svc, "market_compare", snapshot_ids=[a, b])
    assert r["series"] == ["fake.up", "fake.down"] and r["points"] and r["correlation"]["fake.up"]["fake.down"] is not None


def test_compare_refuses_mixed_currency_unless_allowed(svc, fetch):
    a = sid(fetch("fake.up"))
    eur = tool(svc, "market_fetch", provider="fake", symbol="fake.flat", currency="EUR")
    b = sid(eur)
    with pytest.raises(MidasError) as e:
        tool(svc, "market_compare", snapshot_ids=[a, b])
    assert e.value.code == "incompatible_series"
    ok = tool(svc, "market_compare", snapshot_ids=[a, b], allow_incompatible=True)
    assert ok["warnings"]


def test_compare_converts_with_fx_and_records_it(svc, fetch):
    usd = sid(fetch("fake.up"))
    eur = sid(tool(svc, "market_fetch", provider="fake", symbol="fake.down", currency="EUR"))
    fx = sid(fetch("fakefx.eurusd"))
    r = tool(svc, "market_compare", snapshot_ids=[usd, eur], convert_to="EUR", fx={usd: fx})
    conv = r["processing"][usd]["conversion"]
    assert conv["converted"] and conv["from"] == "USD" and conv["to"] == "EUR" and conv["fx_snapshot"] == fx and conv["direction"] == "divide"


def test_empty_currency_series_warns(svc, fetch):
    a = sid(fetch("fake.up"))
    m = sid(tool(svc, "market_fetch", provider="fake", symbol="fakemacro.cpi"))
    r = tool(svc, "market_compare", snapshot_ids=[a, m], allow_incompatible=True)
    assert r["warnings"]


def test_snapshots_list_filters(svc, fetch):
    fetch("fake.up"); fetch("fake.down")
    assert tool(svc, "snapshots_list", symbol="fake.up")["total"] == 1
    assert tool(svc, "snapshots_list", provider="fake")["total"] == 2
    assert tool(svc, "snapshots_list", provider="stooq")["total"] == 0


def test_market_search_finds_seeds(svc):
    r = tool(svc, "market_search", query="bitcoin")
    assert any(x["symbol"] == "bitcoin" for x in r["results"])
    r2 = tool(svc, "market_search", query="cpi", provider="fred")
    assert r2["results"] and all(x["provider"] == "fred" for x in r2["results"])
