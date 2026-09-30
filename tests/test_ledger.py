from __future__ import annotations

from midas_hoard.ledger import Ledger, extract_numbers

VALUES = [{"label": "total return", "value": 0.4231, "kind": "observed", "source": "run bt_1"},
          {"label": "sharpe", "value": 1.186, "kind": "derived", "source": "run bt_1"},
          {"label": "last close", "value": 503.3, "kind": "observed", "source": "snp_1"}]


def ledger():
    return Ledger(VALUES, {"E1": "Inflation was 3.2% in March 2024 according to the release."}, dates=["2024-03-31"])


def statuses(text):
    return {r["text"]: r["status"] for r in ledger().check(text)["detail"]}


def test_observed_number_matches_with_rounding():
    res = ledger().check("Sharpe was 1.19 and the last close 503.")
    assert res["ok"], res["unmatched"]


def test_percent_scaling_matches_fraction():
    assert ledger().check("Total return of 42.3% over the period.")["ok"]


def test_unmatched_number_is_flagged():
    res = ledger().check("Sharpe was 2.7 over the period.")
    assert not res["ok"] and res["unmatched"][0]["text"].startswith("2.7")


def test_proposed_tag_is_allowed():
    res = ledger().check("Consider a stop at 480 [proposed] below the last close.")
    assert res["ok"] and res["counts"].get("proposed") == 1


def test_cited_tag_must_match_the_cited_text():
    assert ledger().check("Inflation was 3.2% [E1].")["ok"]
    res = ledger().check("Inflation was 9.9% [E1].")
    assert not res["ok"] and res["unmatched"][0]["status"] == "cite_mismatch"


def test_small_counts_are_trivial():
    res = ledger().check("There are 4 risks and 6 assumptions.")
    assert res["ok"] and res["counts"].get("trivial") == 2


def test_dates_are_checked():
    assert ledger().check("Data ends 2024-03-31.")["ok"]
    bad = ledger().check("Data ends 2031-01-05.")
    assert not bad["ok"] and bad["unmatched"][0]["status"] == "unmatched_date"


def test_extract_numbers_handles_units_and_tags():
    nums = extract_numbers("Up 12.5% and 1,200 units [E2].")
    assert [n["value"] for n in nums][:2] == [12.5, 1200.0]
    assert nums[0]["unit"] == "%"
