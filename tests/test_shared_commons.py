"""What comes from Hoard Link: the one-instance start, the stable token, the shared bridge, the shared HTTP client behind the
providers, the atomic data files and the shared amount parser."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from conftest import T0, make_config
from midas_hoard.agent_tools import call_tool
from midas_hoard.errors import MidasError
from midas_hoard.hoard_link import net
from midas_hoard.providers.base import HttpClient
from midas_hoard.services import Services

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def running_app(tmp_path):
    """The real app (``python -m midas_hoard``) in a child process on a free port."""
    port = net.free_port()
    env = {**os.environ, "MIDAS_DATA_DIR": str(tmp_path / "data"), "MIDAS_PORT": str(port), "MIDAS_OFFLINE": "1", "HOARD_NO_BROWSER": "1",
           "PYTHONPATH": str(ROOT)}
    command = [sys.executable, "-m", "midas_hoard"]
    child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert net.wait_healthy(f"http://127.0.0.1:{port}", "midas-hoard", timeout=40), "the app did not start"
        yield {"port": port, "env": env, "command": command, "data": tmp_path / "data"}
    finally:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()


def test_second_start_exits_cleanly_and_keeps_the_token(running_app):
    token_file = running_app["data"] / "mcp-token"
    token = token_file.read_text(encoding="utf-8")
    second = subprocess.run(running_app["command"], cwd=ROOT, env=running_app["env"], capture_output=True, text=True, timeout=40)
    assert second.returncode == 0 and "already running" in second.stdout
    assert token_file.read_text(encoding="utf-8") == token
    health = httpx.get(f"http://127.0.0.1:{running_app['port']}/api/health", trust_env=False).json()
    assert health["service"] == "midas-hoard" and health["dataDirConfigured"] is True and health["offline"] is True and "counts" in health
    assert (running_app["data"] / "logs").is_dir()


def test_the_shared_bridge_calls_the_tools_of_the_running_app(running_app, monkeypatch):
    from midas_hoard.hoard_link.bridge import CatalogBridge

    monkeypatch.setenv("MIDAS_DATA_DIR", str(running_app["data"]))
    monkeypatch.setenv("MIDAS_URL", f"http://127.0.0.1:{running_app['port']}")
    monkeypatch.setenv("MIDAS_BRIDGE_AUTOSTART", "0")
    bridge = CatalogBridge(app="midas", service="midas-hoard", package="midas_hoard", default_port=5192, data_dir_env="MIDAS_DATA_DIR",
                           title="Midas's Hoard", root=str(ROOT / "mcp_server.py"))

    async def go():
        tools = await bridge.tools()
        fetched = await bridge.call("market_fetch", {"provider": "fake", "symbol": "fake.up"})
        missing = await bridge.call("thesis_get", {"id": "th_none"})
        return tools, fetched, missing

    tools, fetched, missing = asyncio.run(go())
    assert len(tools) == 23
    assert not fetched.is_error and fetched.body["snapshot"]["rows"] > 1000
    assert missing.is_error and missing.body["code"] == "not_found" and missing.body["hint"]


# ---------------------------------------------------------------- the shared HTTP client behind the providers
def make_client(tmp_path, handler, **kw):
    return HttpClient(tmp_path, transport=httpx.MockTransport(handler), sleep=lambda _s: None, clock=lambda: T0, **kw)


def test_http_client_identifies_itself_caches_and_does_not_cache_html(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        if request.url.path == "/page":
            return httpx.Response(200, text="<html>consent</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text='{"a": 1}', headers={"content-type": "application/json"})

    http = make_client(tmp_path, handler)
    assert http.get("https://api.example.com/data", provider="x").cached is False
    assert http.get("https://api.example.com/data", provider="x").cached is True
    http.get("https://api.example.com/page", provider="x")
    http.get("https://api.example.com/page", provider="x")
    assert len(seen) == 3 and all(ua.startswith("MidasHoard/") for ua in seen)  # the HTML answer was fetched twice
    assert http.cache_stats()["entries"] == 1 and http.misses == 3 and http.hits == 1


def test_http_client_offline_can_be_flipped_while_running(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    http = make_client(tmp_path, handler)
    http.get("https://api.example.com/x", provider="p")
    http.offline = True
    assert http.get("https://api.example.com/x", provider="p").cached is True  # answered from the cache
    with pytest.raises(MidasError) as info:
        http.get("https://api.example.com/other", provider="p")
    assert info.value.code == "provider_unavailable" and "offline" in info.value.message and info.value.status == 502
    assert len(calls) == 1
    http.offline = False
    assert http.get("https://api.example.com/other", provider="p").status == 200


def test_http_client_reports_a_rate_limit_with_its_retry_after(tmp_path):
    http = make_client(tmp_path, lambda r: httpx.Response(429, headers={"retry-after": "30"}))
    with pytest.raises(MidasError) as info:
        http.get("https://api.example.com/x", provider="coingecko")
    assert info.value.code == "rate_limited" and info.value.status == 429 and info.value.details["retry_after"] == "30"
    assert "30 s" in info.value.hint and info.value.details["provider"] == "coingecko"


def test_http_client_retries_a_server_error_once_then_uses_the_stale_copy(tmp_path):
    state = {"ok": True, "calls": 0}
    clock = {"now": T0}

    def handler(request):
        state["calls"] += 1
        return httpx.Response(200, text="fresh", headers={"content-type": "text/plain"}) if state["ok"] else httpx.Response(503)

    http = HttpClient(tmp_path, ttl=10, transport=httpx.MockTransport(handler), sleep=lambda _s: None, clock=lambda: clock["now"])
    assert http.get("https://api.example.com/x", provider="p").text == "fresh"
    clock["now"] += 1000
    state["ok"] = False
    before = state["calls"]
    stale = http.get("https://api.example.com/x", provider="p")
    assert stale.text == "fresh" and stale.cached is True and state["calls"] - before == 2  # tried twice, then the stale copy
    with pytest.raises(MidasError) as info:
        http.get("https://api.example.com/never-fetched", provider="p")
    assert info.value.code == "provider_unavailable" and "503" in info.value.message


def test_http_client_refuses_private_addresses_by_default(tmp_path):
    http = HttpClient(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x")), resolver=lambda h, p: ["10.0.0.5"])
    with pytest.raises(MidasError) as info:
        http.get("https://api.example.com/x", provider="p")
    assert info.value.code == "provider_unavailable"


def test_the_token_and_url_files_are_stable_and_atomic(tmp_path):
    a = Services(make_config(tmp_path), link=_NoLink(), clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    token = a.token
    a.stop()
    b = Services(make_config(tmp_path), link=_NoLink(), clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    try:
        assert b.token == token and (tmp_path / "data" / "mcp-token").read_text() == token
        assert (tmp_path / "data" / "url").read_text().startswith("http://127.0.0.1:")
        assert sorted(p.name for p in (tmp_path / "data").glob("*.tmp*")) == []
    finally:
        b.stop()


class _NoLink:
    def chat(self, *a, **k):
        raise RuntimeError("offline")

    def status(self):
        return {}

    def close(self):
        pass


def test_old_settings_rows_written_as_plain_text_still_work(tmp_path):
    cfg = make_config(tmp_path)
    cfg.data_dir.mkdir(parents=True)
    first = Services(cfg, link=_NoLink(), clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    # what the previous version wrote: the text 1, a language and an API key made only of digits
    first.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('offline', '1'), ('language', 'en'), ('secret.fred', '123456789012')")
    first.stop()
    second = Services(cfg, link=_NoLink(), clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    try:
        assert second.http.offline is True
        assert second.get_settings()["language"] == "en"
        assert second._provider_key("fred") == "123456789012"
        second.update_settings({"offline": False, "language": "es", "keys": {"alphavantage": "ABCDEFGH1234"}})
        assert second.http.offline is False and second.get_settings()["language"] == "es"
        assert second._provider_key("alphavantage") == "ABCDEFGH1234"
    finally:
        second.stop()


def test_backend_and_run_files_are_written_atomically(tmp_path):
    svc = Services(make_config(tmp_path), link=_NoLink(), clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    try:
        svc.update_settings({"backend": {"url": "http://127.0.0.1:1234"}})
        assert json.loads(svc.config.backend_json_path.read_text(encoding="utf-8")) == {"url": "http://127.0.0.1:1234"}
        sid = call_tool(svc, "market_fetch", {"provider": "fake", "symbol": "fake.up"})["snapshot"]["id"]
        assert sid and (svc.config.snapshots_dir / f"{sid}.csv").read_text(encoding="utf-8").startswith("date,")
        leftovers = [p for p in svc.config.data_dir.rglob("*") if p.is_file() and ".tmp" in p.name]
        assert leftovers == []
    finally:
        svc.stop()


def test_the_ledger_reads_numbers_with_the_shared_parser():
    from midas_hoard.ledger import extract_numbers

    got = {n["text"]: (n["value"], n["decimals"]) for n in extract_numbers("EUR/USD 1.085, volume 1,250, rate 12,5%, level 0,123, price 12.50")}
    assert got["1.085"] == (1.085, 3) and got["1,250"] == (1250.0, 0) and got["12,5%"] == (12.5, 1)
    assert got["0,123"] == (0.123, 3) and got["12.50"] == (12.5, 2)  # "0,123" used to be dropped


def test_the_launcher_script_imports_the_shared_net_helpers():
    import importlib.util

    spec = importlib.util.spec_from_file_location("midas_launch_script", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # a deleted helper module would fail here, not on the user's double click
    assert module.SERVICE == "midas-hoard" and callable(module.main)
