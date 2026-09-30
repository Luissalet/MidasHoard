from __future__ import annotations

import pytest

from conftest import T0, tool
from midas_hoard.errors import MidasError


def mk(svc, fetch, rules=None, as_of="2023-06-30", horizon="", assets=None, **kw):
    fetch("fake.up")
    return tool(svc, "thesis_create", title="Trend persists", claim="fake.up keeps rising", rival="It is noise from the generator seed, nothing more.",
                as_of=as_of, horizon=horizon, assets=assets or ["fake.up"], rules=rules if rules is not None else ["close(fake.up) < 50"], **kw)


def test_create_requires_a_rival(svc):
    with pytest.raises(Exception) as e:
        tool(svc, "thesis_create", title="abc", claim="claim", rival="short")
    assert "rival" in str(e.value).lower()


def test_create_validates_rules(svc, fetch):
    with pytest.raises(MidasError) as e:
        mk(svc, fetch, rules=["close(fake.up) <"])
    assert e.value.code == "bad_rule"


def test_create_defaults_and_ref(svc, fetch):
    t = mk(svc, fetch, horizon="12m")
    assert t["status"] == "open" and t["ref"] == f"hoard://midas/thesis/{t['id']}"
    assert t["horizon_end"] == "2024-06-30"
    assert t["rules"][0]["id"] == "r1"


def test_get_list_update_delete(svc, fetch):
    t = mk(svc, fetch)
    got = tool(svc, "thesis_get", id=t["id"])
    assert got["title"] == "Trend persists"
    assert tool(svc, "thesis_list")["total"] == 1 if "total" in tool(svc, "thesis_list") else True
    assert tool(svc, "thesis_list", status="open")["theses"]
    assert not tool(svc, "thesis_list", status="archived")["theses"]
    assert tool(svc, "thesis_list", query="trend")["theses"]
    up = tool(svc, "thesis_update", id=t["id"], status="confirmed", notes="n")
    assert up["status"] == "confirmed"
    with pytest.raises(MidasError) as e:
        tool(svc, "thesis_update", id=t["id"], action="delete")
    assert e.value.code == "confirm_required"
    tool(svc, "thesis_update", id=t["id"], action="delete", confirm=True)
    with pytest.raises(MidasError) as e:
        tool(svc, "thesis_get", id=t["id"])
    assert e.value.code == "not_found"


def test_evidence_metric_is_cut_at_thesis_as_of(svc, fetch):
    t = mk(svc, fetch, as_of="2019-12-31")
    sid = svc.store.latest_for_symbol("fake.up")["id"]
    ev = tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="snapshot_metric", snapshot_id=sid)
    m = ev["added"]["metric"]
    assert m["as_of"] == "2019-12-31"
    full_last = float(svc.store.load(sid).df["close"].iloc[-1])
    assert m["value"] != full_last
    cut = svc.store.load(sid, as_of="2019-12-31").df["close"].iloc[-1]
    assert abs(m["value"] - cut) < 1e-9


def test_evidence_labels_and_delete(svc, fetch):
    t = mk(svc, fetch)
    a = tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="note", title="First note", quote="text of the note")
    b = tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="against", kind="url", title="b", ref="https://example.org/x")
    assert a["added"]["label"] == "E1" and b["added"]["label"] == "E2"
    with pytest.raises(MidasError):
        tool(svc, "thesis_evidence_add", thesis_id=t["id"], action="delete", evidence_id=a["added"]["id"])
    tool(svc, "thesis_evidence_add", thesis_id=t["id"], action="delete", evidence_id=a["added"]["id"], confirm=True)
    assert len(tool(svc, "thesis_get", id=t["id"])["evidence"]) == 1


def test_evidence_requires_side_and_kind(svc, fetch):
    t = mk(svc, fetch)
    with pytest.raises(MidasError) as e:
        tool(svc, "thesis_evidence_add", thesis_id=t["id"], kind="note")
    assert e.value.code == "invalid_request"


def test_check_clear_then_idempotent(svc, fetch):
    t = mk(svc, fetch)
    a = tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
    assert not a["tripped"] and a["status"] == "open" and a["check"]["results"][0]["state"] == "clear"
    b = tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
    assert b["reused"] is True and b["check"]["id"] == a["check"]["id"]
    assert len(tool(svc, "thesis_get", id=t["id"], history=True)["checks"]) == 1


def test_check_trips_invalidates_and_emits_once(svc, fetch):
    events = []
    svc.theses.emit = lambda type_, data: events.append((type_, data))
    t = mk(svc, fetch, rules=["close(fake.up) > 100"])
    a = tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
    assert a["tripped"] and a["status"] == "invalidated"
    tool(svc, "thesis_check", id=t["id"], as_of="2024-07-31", refresh=False)
    invalidated = [e for e in events if e[0] == "midas.thesis.invalidated"]
    assert len(invalidated) == 1 and invalidated[0][1]["thesis"] == t["id"]
    assert any(e[0] == "midas.thesis.created" for e in events)


def test_check_date_bounds(svc, fetch):
    t = mk(svc, fetch)
    with pytest.raises(MidasError):
        tool(svc, "thesis_check", id=t["id"], as_of="2023-01-01", refresh=False)  # before thesis as_of
    with pytest.raises(MidasError):
        tool(svc, "thesis_check", id=t["id"], as_of="2999-01-01", refresh=False)  # in the future


def test_horizon_expiry(svc, fetch):
    t = mk(svc, fetch, horizon="6m", rules=["close(fake.up) < 50"])
    res = tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
    assert res["status"] == "expired"


def test_check_without_snapshot_reports_no_data_or_error(svc):
    t = tool(svc, "thesis_create", title="No data yet", claim="claim text", rival="Some rival hypothesis that fits the facts.", as_of="2023-06-30",
             assets=["zzz.us"], rules=["close(zzz.us) < 1"])
    try:
        res = tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
        assert res["check"]["results"][0]["state"] in ("no_data", "error")
        assert res["status"] == "open"
    except MidasError as e:
        assert e.code in ("no_data", "not_found", "provider_unavailable")


def test_rules_can_be_replaced(svc, fetch):
    t = mk(svc, fetch)
    up = tool(svc, "thesis_update", id=t["id"], rules=["close(fake.up) < 10", "yoy(fake.up) < -10"])
    assert [r["id"] for r in up["rules"]] == ["r1", "r2"]
