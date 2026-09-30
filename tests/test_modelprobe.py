from __future__ import annotations

import time

from conftest import T0, make_config
from midas_hoard.modelprobe import ModelProbe
from midas_hoard.services import Services


class SlowLink:
    def __init__(self, delay=2.0, state="unresolved"):
        self.delay, self.state, self.status_calls, self.chat_calls = delay, state, 0, 0

    def status(self):
        self.status_calls += 1
        time.sleep(self.delay)
        return {"llm": {"state": self.state, "reason": "Faustus not reachable"}}

    def chat(self, *a, **k):
        self.chat_calls += 1
        raise RuntimeError("no model")

    def close(self):
        pass


def test_status_does_not_block_on_a_slow_probe(tmp_path):
    link = SlowLink(2.0)
    svc = Services(make_config(tmp_path), link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    try:
        t = time.perf_counter()
        first = svc.status()
        assert time.perf_counter() - t < 1.5
        assert first["models"] == {"state": "probing"}
        svc.model_probe.wait_idle()
        t = time.perf_counter()
        second = svc.status()
        assert time.perf_counter() - t < 0.2
        assert second["models"]["llm"]["state"] == "unresolved"
        assert link.status_calls == 1
    finally:
        svc.stop()


def test_ttl_refresh_is_stale_while_revalidate():
    now = [0.0]
    calls = []
    probe = ModelProbe(lambda: calls.append(1) or {"n": len(calls)}, ttl=60, clock=lambda: now[0])
    assert probe.get(2) == {"n": 1}
    now[0] = 30
    assert probe.get(2) == {"n": 1} and len(calls) == 1
    now[0] = 61
    assert probe.get(2) == {"n": 1}  # stale answer now, refresh in the background
    probe.wait_idle()
    assert probe.get(2) == {"n": 2}
    probe.close()


def test_probe_errors_are_cached_not_raised():
    def boom():
        raise RuntimeError("hub down")

    probe = ModelProbe(boom)
    assert probe.get(2) == {"error": "hub down"}
    probe.close()


def test_committee_reuses_the_cached_resolution(tmp_path):
    from conftest import tool
    from test_committee import seed

    link = SlowLink(0.3)
    svc = Services(make_config(tmp_path), link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None)
    try:
        t = seed(svc)
        for _ in range(3):
            res = tool(svc, "committee_run", thesis_id=t["id"])
            assert res["mode"] == "material" and "model unavailable" in res["reason"]
        assert link.status_calls == 1  # one probe for all three runs
        assert link.chat_calls == 0    # known-down model: no per-call re-probing through chat
    finally:
        svc.stop()
