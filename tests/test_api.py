from __future__ import annotations

import copy

from midas_hoard.strategy import EXAMPLE_SPEC


def test_health_is_cheap_and_has_family_block(client):
    body = client.get("/api/health").json()
    assert body["service"] == "midas-hoard" and body["version"] and "hoard_link" in body and "counts" in body


def test_status(client):
    body = client.get("/api/status").json()
    assert body["schema_version"] >= 3 and body["providers"] and "not advice" in body["disclaimer"].lower()


def test_agent_tools_endpoint(client):
    body = client.get("/api/agent/tools").json()
    assert body["instructions"] and len(body["tools"]) == 23


def test_agent_call_requires_token(client):
    r = client.post("/api/agent/call", json={"name": "midas_status"})
    assert r.status_code == 401
    r = client.post("/api/agent/call", json={"name": "midas_status"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = client.post("/api/agent/call", json={"name": "midas_status"}, headers=client.bearer)
    assert r.status_code == 200 and r.json()["service"] == "midas-hoard"


def test_agent_call_errors_are_structured(client):
    r = client.post("/api/agent/call", json={"name": "nope"}, headers=client.bearer)
    assert r.status_code == 404
    r = client.post("/api/agent/call", json={"name": "thesis_get", "arguments": {"id": "th_none"}}, headers=client.bearer)
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found" and body["hint"] and body["error"]
    r = client.post("/api/agent/call", json={"name": "market_series", "arguments": {}}, headers=client.bearer)
    assert r.status_code == 400 and "snapshot_id" in r.json()["error"]
    r = client.post("/api/agent/call", json={"name": "thesis_update", "arguments": {"id": "x", "action": "delete"}}, headers=client.bearer)
    assert r.json()["code"] == "confirm_required"


def test_agent_calls_are_recorded(client):
    from midas_hoard.hoard_link import family
    seen = []
    orig = family.record_call
    family.record_call = lambda *a, **k: seen.append((a, k))
    try:
        client.post("/api/agent/call", json={"name": "midas_status", "caller": "tester"}, headers=client.bearer)
    finally:
        family.record_call = orig
    assert seen and seen[0][0][0] == "midas_status" and seen[0][0][1] is True and seen[0][1]["caller"] == "tester"


def test_token_persists_across_services(config, link):
    from conftest import T0
    from midas_hoard.services import Services
    a = Services(config, link=link, clock_fn=lambda: T0)
    tok = a.token
    a.stop()
    b = Services(config, link=link, clock_fn=lambda: T0)
    assert b.token == tok and len(tok) >= 32
    assert (config.data_dir / "mcp-token").read_text(encoding="utf-8").strip() == tok
    b.stop()


def test_full_market_flow_over_http(client):
    r = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"})
    assert r.status_code == 200
    sid = r.json()["snapshot"]["id"]
    assert client.get("/api/snapshots", params={"provider": "fake"}).json()["total"] == 1
    s = client.post("/api/market/series", json={"snapshot_id": sid, "limit": 10, "returns": "simple"}).json()
    assert len(s["points"]) == 10 and "return" in s["columns"]
    other = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.down"}).json()["snapshot"]["id"]
    c = client.post("/api/market/compare", json={"snapshot_ids": [sid, other]}).json()
    assert c["series"] == ["fake.up", "fake.down"]
    assert client.get("/api/market/search", params={"q": "bitcoin"}).json()["results"]
    assert client.get("/api/market/providers").json()["providers"]


def test_market_errors_are_json_with_code(client):
    r = client.post("/api/market/series", json={"snapshot_id": "snp_none"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = client.post("/api/market/fetch", json={"provider": "csv", "symbol": "x", "csv_text": "date,close\n2024-01-01,1\n2024-01-02,2\n"})
    assert r.status_code == 400 and r.json()["code"] == "declaration_required"
    r = client.post("/api/market/series", json={})
    assert r.status_code == 400 and "error" in r.json()


def test_thesis_flow_over_http(client):
    client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"})
    t = client.post("/api/theses", json={"title": "Trend persists", "claim": "fake.up keeps rising", "as_of": "2023-06-30", "assets": ["fake.up"],
                                         "rival": "Pure noise from a seeded generator.", "rules": ["close(fake.up) < 50"]}).json()
    tid = t["id"]
    assert client.get("/api/theses").json()["theses"][0]["id"] == tid
    assert client.get(f"/api/theses/{tid}").json()["rules"]
    ev = client.post(f"/api/theses/{tid}/evidence", json={"side": "against", "kind": "note", "title": "Note", "quote": "a caveat"}).json()
    assert ev["added"]["label"] == "E1"
    chk = client.post(f"/api/theses/{tid}/check", json={"as_of": "2024-06-30", "refresh": False}).json()
    assert chk["status"] == "open"
    com = client.post(f"/api/theses/{tid}/committee", json={"use_model": False}).json()
    assert com["mode"] == "material"
    assert client.patch(f"/api/theses/{tid}", json={"notes": "hi"}).json()["notes"] == "hi"
    rep = client.post("/api/reports", json={"kind": "thesis", "id": tid}).json()
    assert tid in rep["markdown"]
    assert client.delete(f"/api/theses/{tid}/evidence/{ev['added']['id']}").status_code == 200
    assert client.delete(f"/api/theses/{tid}").status_code == 200
    assert client.get(f"/api/theses/{tid}").status_code == 404


def test_rule_validation_endpoint(client):
    ok = client.post("/api/rules/validate", json={"rule": "close(aapl.us) < sma(aapl.us, 50)"}).json()
    assert ok["ok"] and ok["symbols"] == ["aapl.us"]
    bad = client.post("/api/rules/validate", json={"rule": "close(x) <"}).json()
    assert not bad["ok"] and bad["issues"][0]["hint"] is not None


def test_lab_flow_over_http(client):
    sid = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"}).json()["snapshot"]["id"]
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    assert client.get("/api/lab/example").json()["spec"]
    assert client.post("/api/lab/validate", json={"spec": spec}).json()["ok"]
    bad = client.post("/api/lab/validate", json={"spec": {**spec, "universe": ["snp_no"]}}).json()
    assert not bad["ok"]
    saved = client.post("/api/lab/strategies", json={"spec": spec, "note": "n"}).json()
    assert client.get("/api/lab/strategies").json()["count"] == 1
    assert client.get(f"/api/lab/strategies/{saved['id']}").json()["spec"]
    run = client.post("/api/lab/backtests", json={"spec": spec, "holdout_fraction": 0.2}).json()
    rid = run["run_id"]
    v = client.post(f"/api/lab/backtests/{rid}/validate", json={"methods": ["bootstrap"], "n": 100}).json()
    assert v["results"]["bootstrap"]["n"] == 100
    assert client.get("/api/lab/experiments").json()["total"] >= 2
    assert client.get(f"/api/lab/experiments/{rid}").json()["run_id"] == rid
    assert client.post("/api/reports", json={"kind": "run", "id": rid}).status_code == 200
    assert client.delete(f"/api/lab/strategies/{saved['id']}").status_code == 200


def test_portfolio_flow_over_http(client):
    sid = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"}).json()["snapshot"]["id"]
    body = {"currency": "USD", "holdings": [{"symbol": "fake.up", "quantity": 3, "snapshot_id": sid, "currency": "USD"}]}
    assert client.put("/api/portfolios/main", json=body).json()["count"] == 1
    assert client.get("/api/portfolios").json()["portfolios"][0]["name"] == "main"
    a = client.post("/api/portfolios/main/analyze", json={}).json()
    assert a["total_value"] > 0
    assert client.post("/api/reports", json={"kind": "portfolio", "id": "main"}).status_code == 200
    assert client.delete("/api/portfolios/main").status_code == 200
    assert client.get("/api/portfolios").json()["portfolios"] == []


def test_settings_roundtrip(client):
    s = client.get("/api/settings").json()
    assert s["language"] == "es" and s["offline"] is False
    s = client.put("/api/settings", json={"language": "en", "offline": True}).json()
    assert s["language"] == "en" and s["offline"] is True
    s = client.put("/api/settings", json={"clear_cache": True, "offline": False}).json()
    assert s["offline"] is False and s["cache"]["entries"] == 0 if "entries" in s["cache"] else True


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]


def test_spa_fallback_serves_index_or_503(client):
    r = client.get("/some/page")
    assert r.status_code in (200, 503)


def test_bad_host_is_refused(client):
    r = client.get("/api/health", headers={"host": "evil.example"})
    assert r.status_code in (400, 403, 421)


def test_portfolio_get_returns_holdings(client):
    sid = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"}).json()["snapshot"]["id"]
    client.put("/api/portfolios/p", json={"currency": "USD", "holdings": [{"symbol": "fake.up", "quantity": 2, "snapshot_id": sid, "currency": "USD"}]})
    got = client.get("/api/portfolios/p").json()
    assert got["name"] == "p" and got["currency"] == "USD" and got["holdings"][0]["quantity"] == 2
    assert client.get("/api/portfolios/none").status_code == 404
