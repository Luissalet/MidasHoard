from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from midas_hoard.agent_tools import call_tool  # noqa: E402
from midas_hoard.config import Config  # noqa: E402
from midas_hoard.main import create_app  # noqa: E402
from midas_hoard.services import Services  # noqa: E402

T0 = 1_790_000_000.0


@dataclass
class FakeReply:
    text: str
    model: str = "fake-model"


class FakeLink:
    """Sync-shaped model double: hands back scripted replies in order, then repeats the last one."""

    def __init__(self, replies: list[Any] | None = None, fail: bool = False):
        self.replies = list(replies or [])
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.fail:
            raise RuntimeError("no model")
        if not self.replies:
            raise RuntimeError("no scripted reply")
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return FakeReply(item if isinstance(item, str) else json.dumps(item))

    def status(self):
        return {"fake": True}

    def close(self):
        pass


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(data_dir=tmp_path / "data", port=0, port_strict=False, data_dir_configured=True)
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def link():
    return FakeLink(fail=True)


@pytest.fixture
def svc(config, link):
    s = Services(config, link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    yield s
    s.stop()


@pytest.fixture
def client(config, link):
    services = Services(config, link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    app = create_app(config, services=services)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.svc = services
        c.bearer = {"Authorization": f"Bearer {services.token}"}
        yield c


def tool(svc: Services, tool_name: str, /, **arguments) -> Any:
    return call_tool(svc, tool_name, arguments)


@pytest.fixture
def fetch(svc):
    """fetch('fake.up') -> snapshot meta dict."""
    def _fetch(symbol: str = "fake.up", provider: str = "fake", **kw):
        return tool(svc, "market_fetch", provider=provider, symbol=symbol, **kw)
    return _fetch


def make_snapshot(sid: str, closes, start: str = "2020-01-01", currency: str = "USD", symbol: str | None = None, frequency: str = "D",
                  ppy: float = 252.0, unit: str | None = None):
    """An in-memory Snapshot with OHLC equal to close, on business days."""
    import numpy as np
    import pandas as pd

    from midas_hoard.snapshots import Snapshot

    closes = np.asarray(list(closes), dtype=float)
    idx = pd.bdate_range(start, periods=len(closes)) if frequency == "D" else pd.date_range(start, periods=len(closes), freq="ME")
    df = pd.DataFrame({"close": closes, "open": closes, "high": closes, "low": closes, "volume": 1.0}, index=idx)
    meta = {"id": sid, "symbol": symbol or sid, "provider": "fake", "currency": currency, "frequency": frequency, "adjusted": False,
            "unit": unit if unit is not None else currency, "periods_per_year": ppy, "kind": "ohlcv", "sha256": "0" * 64,
            "fetched_at": "2026-01-01T00:00:00Z", "actual_start": str(idx[0].date()), "actual_end": str(idx[-1].date()), "rows": len(df)}
    return Snapshot(meta, df)


class RoleLink:
    """Answers by role (advocate FOR/AGAINST, risk reviewer, arbiter) so parallel calls stay deterministic."""

    def __init__(self, handlers: dict[str, Any]):
        self.handlers = handlers
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        self.calls.append({"system": system, "payload": json.loads(messages[1]["content"]), **kwargs})
        if "arbiter" in system.lower()[:40]:
            role = "arbiter"
        elif "risk reviewer" in system.lower()[:60]:
            role = "risk"
        elif "argue FOR" in system:
            role = "for"
        else:
            role = "against"
        out = self.handlers[role]
        out = out() if callable(out) else out
        return FakeReply(out if isinstance(out, str) else json.dumps(out))

    def status(self):
        return {"fake": True}

    def close(self):
        pass
