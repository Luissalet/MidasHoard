"""The lab report contract: fixed sections, tagged claims, a rule-based decision, no invented numbers."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import tool
from midas_hoard import labreport as lr
from midas_hoard.strategy import EXAMPLE_SPEC


def good_facts(**over):
    """Facts under which every adoption condition is met."""
    facts = {
        "bars": 900, "years": 3.6, "annualised_reliable": True, "trades": 60, "sharpe": 1.1, "total_return": 0.8,
        "health": {"status": "pass", "min_coverage": 0.97, "issues": []}, "has_validation": True,
        "validation": {
            "permutation": {"p_value": 0.01}, "walk_forward": {"positive_windows": 4, "of_windows": 5, "stitched_oos": {"sharpe": 0.8}},
            "bootstrap": {"includes_zero": False, "sharpe": 1.1}, "multiple_testing": {"p_value_bonferroni": 0.02, "haircut_sharpe_annualised": 0.6, "variants_tried": 2, "warning_level": "none"},
            "holdout": {"status": "opened", "peek_number": 1, "metrics": {"total_return": 0.1, "sharpe": 0.7}, "dev_metrics": {"sharpe": 1.1}},
        },
        "variants": {"effective": 2, "tried": 2, "warning_level": "none"}, "duplicate_risk": "low",
    }
    for k, v in over.items():
        facts[k] = v
    return facts


def with_val(**methods):
    f = good_facts()
    f["validation"] = {**f["validation"], **methods}
    return f


def ids(d):
    return {r["id"] for r in d["reasons"]}


def test_every_condition_met_is_adopt():
    d = lr.decide(good_facts())
    assert d["decision"] == "adopt" and not d["reasons"] and d["supports"] and d["label"] == {"en": "Adopt", "es": "Adoptar"}


def test_decision_is_deterministic():
    assert lr.decide(good_facts()) == lr.decide(copy.deepcopy(good_facts()))


def test_no_validation_is_investigate_never_adopt():
    d = lr.decide(good_facts(validation={}, has_validation=False))
    assert d["decision"] == "investigate" and "validation_missing" in ids(d)


@pytest.mark.parametrize("p, expected", [(0.049, "adopt"), (0.05, "review"), (0.099, "review"), (0.10, "investigate"), (0.6, "investigate")])
def test_permutation_p_boundaries(p, expected):
    assert lr.decide(with_val(permutation={"p_value": p}))["decision"] == expected


@pytest.mark.parametrize("pos, expected", [(5, "adopt"), (4, "adopt"), (3, "review"), (2, "investigate"), (1, "investigate")])
def test_walk_forward_few_positive_windows(pos, expected):
    wf = {"positive_windows": pos, "of_windows": 5, "stitched_oos": {"sharpe": 0.8}}
    assert lr.decide(with_val(walk_forward=wf))["decision"] == expected


def test_walk_forward_with_no_positive_window_rejects():
    d = lr.decide(with_val(walk_forward={"positive_windows": 0, "of_windows": 5, "stitched_oos": {"sharpe": -0.4}}))
    assert d["decision"] == "reject" and "wf_no_positive" in d["because"]


def test_two_windows_none_positive_is_not_enough_to_reject():
    d = lr.decide(with_val(walk_forward={"positive_windows": 0, "of_windows": 2, "stitched_oos": {"sharpe": -0.4}}))
    assert d["decision"] == "investigate"


def test_undefined_stitched_oos_sharpe_is_not_zero():
    d = lr.decide(with_val(walk_forward={"positive_windows": 4, "of_windows": 5, "stitched_oos": {"sharpe": None}}))
    assert d["decision"] == "review" and "wf_oos_unknown" in ids(d)


def test_missing_core_validation_caps_at_investigate_and_secondary_at_review():
    f = good_facts()
    del f["validation"]["permutation"]
    assert lr.decide(f)["decision"] == "investigate"
    f = good_facts()
    del f["validation"]["bootstrap"]
    assert lr.decide(f)["decision"] == "review"
    f = good_facts()
    f["validation"]["walk_forward"] = {"error": "too few bars", "code": "no_data"}
    d = lr.decide(f)
    assert d["decision"] == "investigate" and "walk_forward_missing" in ids(d)


def test_data_health_caps_the_decision():
    assert lr.decide(good_facts(health={"status": "warn", "min_coverage": 0.93, "issues": [{"series": "X", "issue": "coverage 93.0 %"}]}))["decision"] == "review"
    d = lr.decide(good_facts(health={"status": "fail", "min_coverage": 0.8, "issues": [{"series": "X", "issue": "coverage 80.0 %"}]}))
    assert d["decision"] == "investigate" and {"health_fail", "low_coverage"} <= ids(d)
    assert lr.decide(good_facts(health={"status": "unknown", "min_coverage": None, "issues": []}))["decision"] == "investigate"


def test_low_coverage_alone_caps_at_investigate():
    assert lr.decide(good_facts(health={"status": "pass", "min_coverage": 0.89, "issues": []}))["decision"] == "investigate"
    assert lr.decide(good_facts(health={"status": "pass", "min_coverage": 0.90, "issues": []}))["decision"] == "adopt"


def test_negative_sharpe_rejects_only_with_enough_trustworthy_data():
    assert lr.decide(good_facts(sharpe=-0.2))["decision"] == "reject"
    assert lr.decide(good_facts(sharpe=-0.2, bars=100))["decision"] == "investigate"  # fewer than 120 bars
    assert lr.decide(good_facts(sharpe=-0.2, years=0.6, annualised_reliable=False))["decision"] == "investigate"
    bad_health = {"status": "fail", "min_coverage": 0.7, "issues": []}
    assert lr.decide(good_facts(sharpe=-0.2, health=bad_health))["decision"] == "investigate"


def test_a_missing_sharpe_is_not_a_zero_sharpe():
    d = lr.decide(good_facts(sharpe=None))
    assert d["decision"] == "investigate" and "thin_sample" in ids(d) and "negative_sharpe" not in ids(d)
    d = lr.decide(good_facts(bars=None))
    assert d["decision"] == "investigate"
    d = lr.decide(good_facts(trades=None))
    assert d["decision"] == "investigate" and "trades_unknown" in ids(d)


def test_holdout_states():
    sealed = lr.decide(with_val(holdout={"status": "sealed", "holdout_start": "2024-01-01"}))
    assert sealed["decision"] == "review" and "holdout_sealed" in ids(sealed)
    none = lr.decide(with_val(holdout={"status": "none"}))
    assert none["decision"] == "review" and "holdout_none" in ids(none)
    failed = lr.decide(with_val(holdout={"status": "opened", "peek_number": 1, "metrics": {"total_return": -0.05, "sharpe": -0.3}, "dev_metrics": {"sharpe": 1.1}}))
    assert failed["decision"] == "reject" and "holdout_failed" in failed["because"]
    peeked = lr.decide(with_val(holdout={"status": "opened", "peek_number": 3, "metrics": {"total_return": 0.1, "sharpe": 0.7}, "dev_metrics": {"sharpe": 1.1}}))
    assert peeked["decision"] == "review"
    nodata = lr.decide(with_val(holdout={"status": "opened", "peek_number": 1, "metrics": {"total_return": None, "sharpe": None}, "dev_metrics": {"sharpe": 1.1}}))
    assert nodata["decision"] == "review" and "holdout_failed" not in ids(nodata)


def test_multiple_testing_gates():
    d = lr.decide(with_val(multiple_testing={"p_value_bonferroni": 0.2, "haircut_sharpe_annualised": 0.3, "variants_tried": 8, "warning_level": "moderate"}))
    assert d["decision"] == "review" and "multiple_testing_p" in ids(d)
    d = lr.decide(with_val(multiple_testing={"p_value_bonferroni": 0.01, "haircut_sharpe_annualised": 0.0, "variants_tried": 8, "warning_level": "moderate"}))
    assert d["decision"] == "investigate" and "haircut_nonpositive" in ids(d)
    strong = {"p_value_bonferroni": 0.01, "haircut_sharpe_annualised": 0.4, "variants_tried": 25, "warning_level": "strong", "deflated_sharpe": None}
    assert lr.decide(with_val(multiple_testing=strong))["decision"] == "review"
    strong["deflated_sharpe"] = {"probability": 0.97}
    assert lr.decide(with_val(multiple_testing=strong))["decision"] == "adopt"


def test_few_trades_and_high_duplicate_risk():
    assert lr.decide(good_facts(trades=19))["decision"] == "review"
    assert lr.decide(good_facts(trades=4))["decision"] == "investigate"
    d = lr.decide(good_facts(duplicate_risk="high"))
    assert d["decision"] == "review" and "duplicate_high" in ids(d)
    assert lr.decide(good_facts(duplicate_risk="medium"))["decision"] == "adopt"


def test_missing_evidence_never_raises_the_decision():
    rank = {"reject": 0, "investigate": 1, "review": 2, "adopt": 3}
    base = rank[lr.decide(good_facts())["decision"]]
    for method in ("permutation", "walk_forward", "bootstrap", "multiple_testing", "holdout"):
        f = good_facts()
        f["validation"].pop(method)
        assert rank[lr.decide(f)["decision"]] <= base


def test_evidence_quality_is_a_label_not_a_score():
    q = lr.evidence_quality(good_facts())
    assert q["grade"] == "strong" and "score" not in q
    assert lr.evidence_quality(good_facts(validation={}, has_validation=False))["grade"] == "insufficient"


# ------------------------------------------------------------------------------------------------ the report itself
@pytest.fixture
def spec(fetch):
    sid = fetch("fake.up")["snapshot"]["id"]
    s = copy.deepcopy(EXAMPLE_SPEC)
    s["universe"] = [sid]
    return s


def all_claims(report):
    for sec in report["sections"]:
        yield from sec.get("claims", [])
        for g in sec.get("gates", []):
            assert g["kind"] in lr.KINDS
        for t in sec.get("tables", []):
            for r in t["rows"]:
                assert r["kind"] in lr.KINDS


def test_report_has_eight_sections_in_fixed_order_and_tagged_claims(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.25)
    res = tool(svc, "report_export", kind="run", id=run["run_id"], format="json", save=False)
    rep = res["json"]["report"]
    assert [s["id"] for s in rep["sections"]] == ["identity", "summary", "gates", "metrics", "mechanism", "risks", "next", "decision"]
    assert [s["number"] for s in rep["sections"]] == list(range(1, 9))
    kinds = {c["kind"] for c in all_claims(rep)}
    assert kinds <= set(lr.KINDS) and {"observed", "inference", "alternative", "experiment"} <= kinds
    assert rep["decision"]["decision"] == "investigate"  # nothing validated yet
    nxt = next(s for s in rep["sections"] if s["id"] == "next")["claims"]
    assert [c["priority"] for c in nxt] == list(range(1, len(nxt) + 1)) and all(c["kind"] == "experiment" for c in nxt)
    assert run["decision"]["decision"] == "investigate" and run["data_health"]["summary"]["status"] in ("pass", "warn")


def test_identity_states_in_and_out_of_sample(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.25)
    ident = next(s for s in svc.lab.run_report(run["run_id"])["sections"] if s["id"] == "identity")["claims"]
    text = " ".join(c["text"] for c in ident)
    assert "In-sample" in text and "Out-of-sample holdout" in text and "sealed" in text
    spec2 = copy.deepcopy(spec)
    spec2["indicators"]["fast"]["window"] = 12
    run2 = tool(svc, "backtest_run", spec=spec2)
    ident2 = " ".join(c["text"] for c in next(s for s in svc.lab.run_report(run2["run_id"])["sections"] if s["id"] == "identity")["claims"])
    assert "no sealed out-of-sample data" in ident2


def test_markdown_and_html_carry_visible_tags_and_the_decision(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.25)
    md = tool(svc, "report_export", kind="run", id=run["run_id"], format="md", save=False)["markdown"]
    heads = [f"## {i}. {t}" for i, t in enumerate(["Identity and scope", "Summary", "Health gates", "Metrics", "Mechanism and alternatives", "Risks and evidence gaps", "Next experiments", "Decision"], 1)]
    pos = [md.index(h) for h in heads]
    assert pos == sorted(pos)
    for tag in ("**[Observed]**", "**[Inference]**", "**[Alternative]**", "**[Experiment]**"):
        assert tag in md
    assert "Investigate (Investigar)" in md and "not advice" in md and "## Appendix" in md and "Data provenance" in md
    html = tool(svc, "report_export", kind="run", id=run["run_id"], format="html", save=True)
    assert "kind-observed" in html["markdown"] and "decision-investigate" in html["markdown"] and html["path"].endswith(".html")
    with pytest.raises(Exception):
        tool(svc, "report_export", kind="portfolio", id="x", format="html")


def test_report_declares_conventions(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    md = tool(svc, "report_export", kind="run", id=run["run_id"], format="md", save=False)["markdown"]
    assert "negative fraction" in md and "do not halve" in md.lower().replace("does not halve", "do not halve") and "No composite score" in md
    assert "never replaced by 0" in md and "never mixed in one table" in md


def test_nulls_stay_null_and_read_no_data(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    doc = svc.lab._assemble_doc(run["run_id"])
    doc["metrics"] = {**doc["metrics"], "cagr": None, "calmar": None, "hit_rate": None, "sharpe": None, "turnover_per_year": None}
    doc["benchmark"] = {**doc["benchmark"], "sharpe": None, "beta": None, "excess_total_return": None}
    rep = lr.build_report(doc)
    rows = {r["metric"]: r for r in next(s for s in rep["sections"] if s["id"] == "metrics")["tables"][0]["rows"]}
    for name in ("CAGR", "Calmar", "hit rate (trades)", "Sharpe", "turnover per year"):
        assert rows[name]["strategy"] is None and rows[name]["strategy_text"] == "no data", name
    assert rows["Sharpe"]["benchmark"] is None and rows["Sharpe"]["benchmark_text"] == "no data"
    assert rows["Sortino"]["benchmark_text"] == ""  # not applicable is not "no data" either
    md = lr.to_markdown(rep)
    assert "| CAGR | no data |" in md and "0.00%" not in md.split("## 4.")[1].split("## 5.")[0].split("hit rate")[1].split("\n")[0]
    # a missing Sharpe makes the decision "investigate", not a reject on a 0
    assert rep["decision"]["decision"] == "investigate"
    assert lr.fmt_pct(None) == "no data" and lr.fmt_num(float("nan")) == "no data" and lr.fmt_pct(0.0) == "0.00%"


def test_excess_return_is_null_when_a_side_is_missing():
    # the engine no longer turns a missing total return into 0 when comparing with the benchmark
    import inspect

    from midas_hoard import backtest
    src = inspect.getsource(backtest.run_backtest)
    assert "or 0.0) - (bench_metrics" not in src


def test_validation_updates_the_report_and_the_decision(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.3)
    before = svc.lab.run_report(run["run_id"])["decision"]["decision"]
    val = tool(svc, "backtest_validate", run_id=run["run_id"], windows=4, n=200, seed=1)
    assert val["decision"]["decision"] in lr.DECISIONS
    rep = svc.lab.run_report(run["run_id"])
    gates = {g["id"]: g for s in rep["sections"] if s["id"] == "gates" for g in s["gates"]}
    assert gates["permutation"]["status"] != "not_run" and gates["walk_forward"]["status"] != "not_run" and gates["holdout"]["text"] == "sealed, untouched"
    assert before == "investigate"
    assert rep["decision"]["decision"] in ("reject", "investigate", "review")  # the holdout is sealed: never adopt
    report_md = (svc.lab.runs_dir / run["run_id"] / "report.md").read_text(encoding="utf-8")
    assert "## 8. Decision" in report_md and "permutation" in report_md.lower()
    tool(svc, "backtest_validate", run_id=run["run_id"], methods=["bootstrap"], n=100, reveal_holdout=True)
    rep2 = svc.lab.run_report(run["run_id"])
    assert {t["scope"] for s in rep2["sections"] if s["id"] == "metrics" for t in s["tables"]} >= {"in_sample", "walk_forward_oos", "holdout"}
    # the earlier permutation/walk-forward results are kept when a later validation runs only the bootstrap
    gates2 = {g["id"]: g for s in rep2["sections"] if s["id"] == "gates" for g in s["gates"]}
    assert gates2["permutation"]["status"] != "not_run" and gates2["holdout"]["text"].startswith("opened")


def test_experiments_list_run_detail_has_the_decision(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    detail = tool(svc, "experiments_list", run_id=run["run_id"])
    assert detail["decision"]["decision"] == "investigate" and detail["data_health"]["summary"]["status"] and detail["evidence_quality"]["grade"] == "insufficient"


def test_report_json_is_serialisable(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    json.dumps(svc.lab.run_report(run["run_id"]))
