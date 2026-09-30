"""The stdio MCP bridge against a real server process: initialize, tools/list, tools/call."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    env = {**os.environ, "MIDAS_DATA_DIR": str(tmp_path / "data"), "MIDAS_PORT": str(port), "PORT_STRICT": "1", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "midas_hoard"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/api/health", timeout=1, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("server did not start")
        yield url, tmp_path / "data", env
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def rpc(proc, payload):
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_bridge_roundtrip(server):
    url, data, env = server
    benv = {**env, "MIDAS_URL": url, "MIDAS_TOKEN_FILE": str(data / "mcp-token"), "MIDAS_BRIDGE_AUTOSTART": "0"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "mcp_server.py")], cwd=ROOT, env=benv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    try:
        init = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                                                       "clientInfo": {"name": "t", "version": "0"}}})
        assert init["result"]["serverInfo"]["name"] == "midas-hoard"
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
        proc.stdin.flush()
        tools = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        assert len(tools) == 23 and {"market_fetch", "backtest_run"} <= {t["name"] for t in tools}
        ok = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "market_fetch", "arguments": {"provider": "fake", "symbol": "fake.up"}}})
        body = json.loads(ok["result"]["content"][0]["text"])
        assert body["snapshot"]["rows"] > 1000
        err = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "thesis_get", "arguments": {"id": "th_none"}}})
        detail = json.loads(err["result"]["content"][0]["text"])
        assert detail["code"] == "not_found" and detail["hint"]
    finally:
        proc.terminate()
        proc.wait(10)
