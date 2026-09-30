from __future__ import annotations

import copy
import json

import pytest

from conftest import tool
from midas_hoard.errors import MidasError
from midas_hoard.strategy import EXAMPLE_SPEC


def sample_thesis(svc, fetch):
    sid = fetch("fake.up")["snapshot"]["id"]
    t = tool(svc, "thesis_create", title="Trend persists", claim="fake.up keeps rising", rival="It is noise from a seeded generator only.",
             as_of="2023-06-30", assets=["fake.up"], rules=["close(fake.up) < 50"])
    tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="snapshot_metric", snapshot_id=sid, title="Last close")
    tool(svc, "thesis_check", id=t["id"], as_of="2024-06-30", refresh=False)
    return t, sid


def test_thesis_report_has_disclaimer_provenance_and_file(svc, fetch):
    t, sid = sample_thesis(svc, fetch)
    res = tool(svc, "report_export", kind="thesis", id=t["id"])
    md = res["markdown"]
    assert "not advice" in md.lower() and t["id"] in md
    assert sid in md and "sha256" in md.lower() or sid in md
    assert res["path"] and (svc.config.reports_dir / f"thesis-{t['id']}.md").is_file()


def test_thesis_report_json(svc, fetch):
    t, _ = sample_thesis(svc, fetch)
    res = tool(svc, "report_export", kind="thesis", id=t["id"], format="json", save=False)
    assert res["path"] is None and res["json"]["thesis"]["id"] == t["id"]
    json.dumps(res["json"], default=str)


def test_run_report(svc, fetch):
    sid = fetch("fake.up")["snapshot"]["id"]
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    run = tool(svc, "backtest_run", spec=spec)
    res = tool(svc, "report_export", kind="run", id=run["run_id"])
    assert run["run_id"] in res["markdown"] and "not advice" in res["markdown"].lower()
    assert "Sharpe" in res["markdown"] or "sharpe" in res["markdown"]


def test_report_paging(svc, fetch):
    t, _ = sample_thesis(svc, fetch)
    full = tool(svc, "report_export", kind="thesis", id=t["id"], save=False)
    page = tool(svc, "report_export", kind="thesis", id=t["id"], save=False, cursor=0, page_chars=1000)
    assert len(page["markdown"]) <= 1000 and page["total_chars"] == full["total_chars"]


def test_unknown_kind_and_missing_id(svc):
    with pytest.raises(Exception):
        tool(svc, "report_export", kind="nonsense", id="x")
    with pytest.raises(MidasError):
        tool(svc, "report_export", kind="thesis", id="th_missing")
