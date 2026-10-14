"""The lab report contract: eight sections in a fixed order, every statement tagged, the decision computed by rules.

Sections: (1) identity and scope, (2) summary, (3) health gates, (4) metrics, (5) mechanism with alternatives, (6) risks and
evidence gaps, (7) prioritised next experiments, (8) decision.

Every statement is a *claim* with a ``kind``: ``observed`` (read from, or computed on, the stored artifacts), ``inference``
(an interpretation of observations), ``alternative`` (another explanation that fits the same observations) or ``experiment``
(a test that would tell them apart). The decision (reject / investigate / review / adopt) comes from ``decide``, a pure
function of the validations that exist, the data health and the number of variants tried: no model writes or changes it,
and missing evidence can only lower it. A null stays null and is shown as "no data", never as 0.
"""

from __future__ import annotations

import html as html_mod
import math
from typing import Any, Optional

KINDS = ("observed", "inference", "alternative", "experiment")
KIND_LABELS = {"observed": {"en": "Observed", "es": "Observado"}, "inference": {"en": "Inference", "es": "Inferencia"},
               "alternative": {"en": "Alternative", "es": "Alternativa"}, "experiment": {"en": "Experiment", "es": "Experimento"}}
DECISIONS = ("reject", "investigate", "review", "adopt")
DECISION_LABELS = {"reject": {"en": "Reject", "es": "Rechazar"}, "investigate": {"en": "Investigate", "es": "Investigar"},
                   "review": {"en": "Review", "es": "Revisar"}, "adopt": {"en": "Adopt", "es": "Adoptar"}}
DECISION_MEANING = {
    "reject": "The evidence available argues against the strategy; stop spending trials on it.",
    "investigate": "The evidence is missing, thin or not trustworthy yet; this is not a result, it is a to-do list.",
    "review": "The evidence is mostly supportive but at least one adoption condition is not met; a person must look at the gaps.",
    "adopt": "Every adoption condition is met on the stored evidence; still research, not advice.",
}
SECTIONS = (("identity", "Identity and scope", "Identidad y alcance"), ("summary", "Summary", "Resumen"),
            ("gates", "Health gates", "Puertas de salud"), ("metrics", "Metrics", "Métricas"),
            ("mechanism", "Mechanism and alternatives", "Mecanismo y alternativas"), ("risks", "Risks and evidence gaps", "Riesgos y huecos de evidencia"),
            ("next", "Next experiments", "Experimentos siguientes"), ("decision", "Decision", "Decisión"))
CONVENTIONS = {
    "drawdown_sign": "Drawdown is a negative fraction of the previous equity peak (-0.25 is a 25 % fall); a more negative value is a deeper drawdown. It is never shown as a positive magnitude.",
    "turnover": "Turnover is the sum of |change in target weight| over assets and bars, divided by the years of the window (Midas does not halve it, so a full switch from cash to 100 % invested counts 1.0).",
    "score": "No composite score is computed. The evidence-quality grade is a rule-based label of how much was tested, not a verdict and not a prediction; the decision is the rule outcome of section 8.",
    "nulls": "A missing value stays missing and is shown as 'no data'; it is never replaced by 0 in a table, a count or a comparison.",
    "windows": "Each table states its own window. Development, walk-forward out-of-sample and holdout figures are never mixed in one table.",
}
THRESHOLDS = {"min_bars": 60, "reject_min_bars": 120, "min_years": 1.0, "min_trades_adopt": 20, "min_trades_review": 5, "perm_adopt": 0.05, "perm_review": 0.10,
              "wf_adopt": 0.7, "wf_review": 0.5, "wf_min_windows": 3, "bonferroni": 0.05, "deflated": 0.95, "strong_variants": 20, "coverage_fail": 0.90}
RULES_VERSION = 1
DISCLAIMER = "Results are historical analyses, not advice. Past performance does not predict future results."


# ---------------------------------------------------------------------------------------------- formatting
def _ok(x: Any) -> bool:
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def fmt_pct(x: Any, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%" if _ok(x) else "no data"


def fmt_num(x: Any, digits: int = 2) -> str:
    return f"{x:,.{digits}f}" if _ok(x) else "no data"


def claim(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    assert kind in KINDS, kind
    return {"kind": kind, "text": text, **extra}


# ---------------------------------------------------------------------------------------------- facts
def latest_validation_results(validations: list[dict[str, Any]]) -> dict[str, Any]:
    """Per method, the result of the most recent validation that ran it. An opened holdout stays opened."""
    out: dict[str, Any] = {}
    for v in validations:
        for method, res in (v.get("results") or {}).items():
            if method == "holdout":
                if (res or {}).get("status") == "opened" or (out.get("holdout") or {}).get("status") != "opened":
                    out["holdout"] = res
            else:
                out[method] = res
    return out


def facts_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    m = doc.get("metrics") or {}
    health = (doc.get("data_health") or {}).get("summary") or {"status": "unknown", "min_coverage": None, "issues": []}
    fam = doc.get("variants") or {}
    return {"bars": (doc.get("window") or {}).get("bars"), "years": m.get("years"), "annualised_reliable": bool(m.get("annualised_reliable")),
            "trades": m.get("trades"), "sharpe": m.get("sharpe"), "total_return": m.get("total_return"), "health": health,
            "validation": latest_validation_results(doc.get("validations") or []), "has_validation": bool(doc.get("validations")),
            "variants": {"effective": fam.get("effective", fam.get("tried")), "tried": fam.get("tried"), "warning_level": fam.get("warning_level")},
            "duplicate_risk": (doc.get("duplicate") or {}).get("level")}


# ---------------------------------------------------------------------------------------------- the decision
def _method(v: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    r = v.get(name)
    return r if isinstance(r, dict) else None


def decide(facts: dict[str, Any]) -> dict[str, Any]:
    """Reject / investigate / review / adopt from the facts alone. Deterministic; caps only ever lower the outcome."""
    T = THRESHOLDS
    rules: list[dict[str, Any]] = []   # fired rules: effect is reject | cap_investigate | cap_review
    passed: list[dict[str, Any]] = []
    needs: list[str] = []
    data_block = False                 # evidence too thin or untrustworthy to call a failure

    def fire(rid: str, effect: str, text: str, need: Optional[str] = None, blocks_reject: bool = False) -> None:
        nonlocal data_block
        rules.append({"id": rid, "effect": effect, "text": text})
        if need and need not in needs:
            needs.append(need)
        if blocks_reject:
            data_block = True

    def ok(rid: str, text: str) -> None:
        passed.append({"id": rid, "effect": "support", "text": text})

    bars, sharpe = facts.get("bars"), facts.get("sharpe")
    health = facts.get("health") or {}
    hstatus, cov = health.get("status", "unknown"), health.get("min_coverage")
    v = facts.get("validation") or {}

    # data and sample
    if not _ok(bars) or bars < T["min_bars"] or not _ok(sharpe):
        fire("thin_sample", "cap_investigate", f"Only {bars if _ok(bars) else 'no data'} bars or no Sharpe ({fmt_num(sharpe)}): too little data to judge.",
             "Use a longer history.", True)
    else:
        ok("sample", f"{bars} bars with a defined Sharpe.")
    years = facts.get("years")
    if not facts.get("annualised_reliable") or (_ok(years) and years < T["min_years"]):
        fire("short_window", "cap_investigate", f"The window is {fmt_num(years)} years (< {T['min_years']:.0f}): annualised figures are noisy.", "Use at least one full year of data.", True)
    if hstatus == "unknown":
        fire("health_unknown", "cap_investigate", "Data health was not computed for this run.", "Run data_health on the run's snapshots.", True)
    elif hstatus == "fail":
        fire("health_fail", "cap_investigate", "At least one series fails a data-health gate: " + "; ".join(f"{i['series']}: {i['issue']}" for i in (health.get("issues") or [])[:3]) + ".",
             "Repair or replace the failing series (see data_health), then re-run.", True)
    elif hstatus == "warn":
        fire("health_warn", "cap_review", "Data health has warnings: " + "; ".join(f"{i['series']}: {i['issue']}" for i in (health.get("issues") or [])[:3]) + ".")
    else:
        ok("health", "Data health passes on every series.")
    if _ok(cov) and cov < T["coverage_fail"]:
        fire("low_coverage", "cap_investigate", f"Coverage {fmt_pct(cov, 1)} is below {T['coverage_fail'] * 100:.0f} %.", "Fill the date gaps with a better source.", True)
    trades = facts.get("trades")
    if not _ok(trades):
        fire("trades_unknown", "cap_investigate", "The number of trades is unknown.", None, True)
    elif trades < T["min_trades_review"]:
        fire("few_trades", "cap_investigate", f"Only {trades} trade(s): the result rests on almost nothing.", "Use a longer window or more assets.", True)
    elif trades < T["min_trades_adopt"]:
        fire("few_trades", "cap_review", f"Only {trades} trades (< {T['min_trades_adopt']}): the result rests on few observations.")
    else:
        ok("trades", f"{trades} trades.")

    # validations
    if not facts.get("has_validation") and not v:
        fire("validation_missing", "cap_investigate", "No validation has been run on this backtest.", "Run backtest_validate (walk-forward, permutation, bootstrap, multiple testing).")
    perm, wf, boot, mt, ho = _method(v, "permutation"), _method(v, "walk_forward"), _method(v, "bootstrap"), _method(v, "multiple_testing"), _method(v, "holdout")
    if v or facts.get("has_validation"):
        for name, res, effect in (("permutation", perm, "cap_investigate"), ("walk_forward", wf, "cap_investigate"), ("bootstrap", boot, "cap_review"),
                                  ("multiple_testing", mt, "cap_review")):
            if res is None or "error" in res:
                why = "was not run" if res is None else f"failed: {res.get('error')}"
                fire(f"{name}_missing", effect, f"{name.replace('_', '-')} {why}.", f"Run backtest_validate with methods=['{name}'].")
    if perm and "error" not in perm:
        p = perm.get("p_value")
        if not _ok(p):
            fire("permutation_p", "cap_investigate", "The permutation test returned no p-value.")
        elif p >= T["perm_review"]:
            fire("permutation_p", "cap_investigate", f"Permutation p = {p:.4f} (>= {T['perm_review']}): random timing with the same exposure does as well.")
        elif p >= T["perm_adopt"]:
            fire("permutation_p", "cap_review", f"Permutation p = {p:.4f} (>= {T['perm_adopt']}): suggestive, not conclusive.")
        else:
            ok("permutation", f"Permutation p = {p:.4f}.")
    reject_wf = False
    if wf and "error" not in wf:
        pos, of = wf.get("positive_windows"), wf.get("of_windows")
        stitched = (wf.get("stitched_oos") or {}).get("sharpe")
        if not _ok(pos) or not _ok(of) or of <= 0:
            fire("wf_windows", "cap_investigate", "Walk-forward returned no window count.")
        else:
            ratio = pos / of
            if pos == 0 and of >= T["wf_min_windows"]:
                reject_wf = True
                fire("wf_no_positive", "reject", f"0 of {of} walk-forward windows were positive.")
            elif ratio < T["wf_review"]:
                fire("wf_few_positive", "cap_investigate", f"Only {pos} of {of} walk-forward windows were positive.", "Find out which regimes lose before spending more variants.")
            elif ratio < T["wf_adopt"]:
                fire("wf_few_positive", "cap_review", f"{pos} of {of} walk-forward windows were positive (< {T['wf_adopt'] * 100:.0f} %).")
            else:
                ok("walk_forward", f"{pos} of {of} walk-forward windows were positive.")
        if not _ok(stitched):
            fire("wf_oos_unknown", "cap_review", "The stitched out-of-sample Sharpe is not defined.")
        elif stitched <= 0:
            fire("wf_oos_nonpositive", "cap_investigate", f"Stitched out-of-sample Sharpe is {stitched:.2f}.")
    if boot and "error" not in boot:
        if boot.get("includes_zero") is not False:
            fire("bootstrap_zero", "cap_review", "The bootstrap confidence interval for the Sharpe includes zero (or is undefined).")
        else:
            ok("bootstrap", "The bootstrap interval for the Sharpe excludes zero.")
    if mt and "error" not in mt:
        pb, hs = mt.get("p_value_bonferroni"), mt.get("haircut_sharpe_annualised")
        if not _ok(pb):
            fire("multiple_testing_p", "cap_review", "No multiple-testing p-value (not enough data for a Sharpe test).")
        elif pb >= T["bonferroni"]:
            fire("multiple_testing_p", "cap_review", f"After correcting for {mt.get('variants_tried')} variant(s) the p-value is {pb:.4f} (>= {T['bonferroni']}).")
        else:
            ok("multiple_testing", f"Bonferroni-adjusted p = {pb:.4f} over {mt.get('variants_tried')} variant(s).")
        if _ok(hs) and hs <= 0:
            fire("haircut_nonpositive", "cap_investigate", "The haircut Sharpe is not positive: nothing survives the multiple-testing correction.")
        dsr = (mt.get("deflated_sharpe") or {}).get("probability")
        if (mt.get("warning_level") == "strong") and (not _ok(dsr) or dsr < T["deflated"]):
            fire("deflated", "cap_review", f"{mt.get('variants_tried')} variants were tried and the deflated Sharpe probability is {fmt_pct(dsr, 1)} (< {T['deflated'] * 100:.0f} %).")
    # holdout
    reject_ho = False
    status = (ho or {}).get("status")
    if status == "opened":
        hm = ho.get("metrics") or {}
        ret, sh = hm.get("total_return"), hm.get("sharpe")
        dev_sh = (ho.get("dev_metrics") or {}).get("sharpe")
        if (not _ok(ret) or not _ok(sh)):
            fire("holdout_unknown", "cap_review", "The opened holdout has no defined return or Sharpe.")
        elif (ret <= 0 or sh <= 0):
            if _ok(dev_sh) and dev_sh > 0:
                reject_ho = True
                fire("holdout_failed", "reject", f"The holdout lost money or has a non-positive Sharpe (return {fmt_pct(ret)}, Sharpe {fmt_num(sh)}) after a positive development Sharpe ({fmt_num(dev_sh)}).")
            else:
                fire("holdout_nonpositive", "cap_investigate", f"Holdout return {fmt_pct(ret)}, Sharpe {fmt_num(sh)}.")
        else:
            ok("holdout", f"Holdout return {fmt_pct(ret)}, Sharpe {fmt_num(sh)}.")
        if _ok(ho.get("peek_number")) and ho["peek_number"] > 1:
            fire("holdout_peeked", "cap_review", f"The holdout was looked at {ho['peek_number']} times: it is a development set now.")
    elif status == "sealed":
        fire("holdout_sealed", "cap_review", "The holdout is still sealed: adoption needs one honest look at it.", "Open it once with backtest_validate(reveal_holdout=true) when the spec is final.")
    elif v or facts.get("has_validation"):
        fire("holdout_none", "cap_review", "The run has no holdout: there is no data the strategy was never fitted on.", "Re-run with holdout_fraction=0.3 and validate that run.")
    if facts.get("duplicate_risk") == "high":
        fire("duplicate_high", "cap_review", "A near-duplicate of this idea exists under another name: the real number of trials is higher than this family shows.",
             "Compare with the similar experiments before trying more variants.")

    # outcome
    if _ok(sharpe) and sharpe <= 0:
        if _ok(bars) and bars >= T["reject_min_bars"]:
            fire("negative_sharpe", "reject", f"Net Sharpe on the development window is {sharpe:.2f} (<= 0) over {bars} bars.")
        else:
            fire("sharpe_nonpositive", "cap_investigate", f"Net Sharpe is {sharpe:.2f} (<= 0) but over {bars if _ok(bars) else 'no data'} bars (< {T['reject_min_bars']}): too short to call it a failure.")
    total = facts.get("total_return")
    if _ok(total) and total <= 0 and _ok(sharpe) and sharpe > 0:
        fire("return_nonpositive", "cap_investigate", f"Net total return is {fmt_pct(total)} (<= 0) although the Sharpe is positive.")
    rejecting = [r for r in rules if r["effect"] == "reject"]
    caps = [r for r in rules if r["effect"].startswith("cap_")]
    if rejecting and not data_block:
        decision, because = "reject", rejecting
    elif any(r["effect"] == "cap_investigate" for r in caps) or rejecting:
        decision, because = "investigate", [r for r in caps if r["effect"] == "cap_investigate"] or rejecting
    elif caps:
        decision, because = "review", caps
    else:
        decision, because = "adopt", passed
    order = {"reject": 0, "cap_investigate": 1, "cap_review": 2}
    rules.sort(key=lambda r: order[r["effect"]])
    return {"decision": decision, "label": DECISION_LABELS[decision], "meaning": DECISION_MEANING[decision], "because": [r["id"] for r in because],
            "reasons": rules, "supports": passed, "needs": needs, "rules_version": RULES_VERSION, "thresholds": THRESHOLDS,
            "method": "deterministic rules over the stored validations, data health and variants tried; no model writes or changes it; missing evidence only lowers it",
            "applied_caps": sorted({r["effect"] for r in caps}), "rejected_by_evidence": bool(rejecting) and not data_block}


def evidence_quality(facts: dict[str, Any]) -> dict[str, Any]:
    """A label for how much was tested (not a score, not a verdict)."""
    v = facts.get("validation") or {}
    methods = [m for m in ("permutation", "walk_forward", "bootstrap", "multiple_testing") if _method(v, m) and "error" not in v[m]]
    opened = (_method(v, "holdout") or {}).get("status") == "opened"
    healthy = (facts.get("health") or {}).get("status") == "pass"
    years, bars = facts.get("years"), facts.get("bars")
    if not _ok(bars) or bars < THRESHOLDS["min_bars"]:
        grade, why = "insufficient", "too few bars"
    elif len(methods) == 4 and opened and healthy and _ok(years) and years >= 3:
        grade, why = "strong", "all four validations ran, the holdout was opened, data health passes and the window is at least 3 years"
    elif len(methods) >= 3 and (facts.get("health") or {}).get("status") in ("pass", "warn"):
        grade, why = "moderate", f"{len(methods)} of 4 validations ran" + ("" if opened else " but no holdout was opened")
    elif methods:
        grade, why = "weak", f"only {len(methods)} of 4 validations ran or data health is not clean"
    else:
        grade, why = "insufficient", "no validation has been run"
    return {"grade": grade, "reason": why, "validations_run": methods, "holdout_opened": opened}


# ---------------------------------------------------------------------------------------------- spec in words
def describe_rule(node: Any) -> str:
    if not isinstance(node, dict):
        return "?"
    for key, joiner in (("all", " AND "), ("any", " OR ")):
        if key in node:
            return "(" + joiner.join(describe_rule(n) for n in node[key]) + ")"
    if "not" in node:
        return "NOT " + describe_rule(node["not"])
    return f"{node.get('left')} {node.get('op')} {node.get('right')}"


def describe_indicator(name: str, d: dict[str, Any]) -> str:
    params = ", ".join(f"{k}={v}" for k, v in d.items() if k not in ("type", "field", "source"))
    return f"{name} = {d.get('type')}({d.get('field', 'close')}{', ' + params if params else ''})"


MECHANISM = {
    "trend": ("trend/momentum", "persistence of returns: prices that moved one way keep moving long enough to pay for the costs of following them"),
    "reversion": ("mean reversion", "overreaction that reverts: stretched prices or oversold readings snap back before costs eat the gain"),
    "volatility": ("volatility", "volatility clustering: risk is predictable enough that changing exposure with it improves the return per unit of risk"),
}
KIND_OF_INDICATOR = {"sma": "trend", "ema": "trend", "roc": "trend", "momentum": "trend", "returns": "trend", "rolling_max": "trend",
                     "rsi": "reversion", "zscore": "reversion", "bollinger": "reversion", "drawdown": "reversion", "rolling_min": "reversion", "vol": "volatility"}


def mechanism_classes(spec: dict[str, Any]) -> list[str]:
    found = []
    for d in (spec.get("indicators") or {}).values():
        c = KIND_OF_INDICATOR.get((d or {}).get("type"))
        if c and c not in found:
            found.append(c)
    if (spec.get("sizing") or {}).get("type") == "vol_target" and "volatility" not in found:
        found.append("volatility")
    return found


# ---------------------------------------------------------------------------------------------- the report
def build_report(doc: dict[str, Any]) -> dict[str, Any]:
    """The contract report (JSON) of one backtest run. ``doc`` is the run's stored document plus its validations, data health and duplicate check."""
    facts = facts_from_doc(doc)
    decision = decide(facts)
    quality = evidence_quality(facts)
    spec, m, b, w = doc["spec"], doc.get("metrics") or {}, doc.get("benchmark") or {}, doc.get("window") or {}
    v = facts["validation"]
    perm, wf, boot, mt, ho = _method(v, "permutation"), _method(v, "walk_forward"), _method(v, "bootstrap"), _method(v, "multiple_testing"), _method(v, "holdout")
    split = doc.get("holdout") or {}
    health = doc.get("data_health") or {"summary": {"status": "unknown", "issues": []}, "series": []}
    dup = doc.get("duplicate") or {}
    fam = doc.get("variants") or {}
    symbols = [s.get("symbol") for s in doc.get("snapshots", []) if s.get("id") in spec.get("universe", [])] or spec.get("universe", [])

    # 1 identity and scope
    ident = [claim("observed", f"Strategy '{spec['name']}' (family '{spec.get('family', spec['name'])}'), spec hash {doc.get('spec_hash')}, run {doc['run_id']}."),
             claim("observed", f"Universe: {', '.join(str(s) for s in symbols)}; benchmark {b.get('symbol') or 'none'}; execution '{spec.get('execution')}', rebalance '{spec.get('rebalance')}', "
                               f"costs {spec.get('costs_bps')} bps + slippage {spec.get('slippage_bps')} bps.")]
    ident.append(claim("observed", f"Evaluated window {w.get('start')} to {w.get('end')} ({w.get('bars')} bars), data from {w.get('data_start')}; cut at {w.get('as_of') or 'the latest data'}."))
    if split and split.get("dev_end"):
        state = "opened" if (ho or {}).get("status") == "opened" else "sealed"
        ident.append(claim("observed", f"In-sample (development): {w.get('data_start')} to {split['dev_end']}. Out-of-sample holdout: {split['holdout_start']} to {split['holdout_end']} "
                                       f"({split['holdout_bars']} bars), {state}; this run never used it."))
    else:
        ident.append(claim("observed", "Every bar of the window was available while the strategy was written: the whole window is in-sample and there is no sealed out-of-sample data."))
    if wf and "error" not in wf and wf.get("windows"):
        ident.append(claim("observed", f"Walk-forward windows {wf['windows'][0]['test_start']} to {wf['windows'][-1]['test_end']} are out-of-sample only for the choice of parameters: "
                                       "the rule logic was written having seen the development data."))
    ident.append(claim("inference", f"Evidence quality: {quality['grade']} ({quality['reason']}). This labels how much was tested; it is not a verdict.", grade=quality["grade"]))

    # 2 summary
    summ = [claim("observed", f"Net of costs the strategy returned {fmt_pct(m.get('total_return'))} (CAGR {fmt_pct(m.get('cagr'))}) with a Sharpe of {fmt_num(m.get('sharpe'))} and a maximum drawdown "
                              f"of {fmt_pct(m.get('max_drawdown'))}; the benchmark returned {fmt_pct(b.get('total_return'))} over the same window."),
            claim("observed", f"{fam.get('tried', 'no data')} variant(s) of the family were tried" + (f" and {fam['related']} near-duplicate(s) under other names count with them" if fam.get("related") else "")
                              + f" (warning level: {fam.get('warning_level', 'no data')}).")]
    summ.append(claim("inference", f"Decision by rules: {DECISION_LABELS[decision['decision']]['en'].lower()} - " + (decision["reasons"][0]["text"] if decision["reasons"] else "every adoption condition is met") + ""))

    # 3 gates
    gates: list[dict[str, Any]] = []
    hs = health["summary"]
    gates.append({"id": "data_health", "label": "Data health", "status": hs.get("status", "unknown"), "kind": "observed",
                  "text": (f"{hs.get('series', 0)} series checked, minimum coverage {fmt_pct(hs.get('min_coverage'), 1)}" if hs.get("series") else "not computed")
                  + ("; " + "; ".join(f"{i['series']}: {i['issue']}" for i in hs.get("issues", [])[:4]) if hs.get("issues") else "")})
    gates.append({"id": "sample", "label": "Sample size", "kind": "observed",
                  "status": "fail" if any(r["id"] in ("thin_sample", "short_window", "few_trades") and r["effect"] == "cap_investigate" for r in decision["reasons"])
                  else "warn" if any(r["id"] == "few_trades" for r in decision["reasons"]) else "pass",
                  "text": f"{w.get('bars', 'no data')} bars, {fmt_num(m.get('years'))} years, {m.get('trades', 'no data')} trades."})

    def gate(gid: str, label: str, res: Optional[dict[str, Any]], status: str, text: str) -> None:
        if res is None:
            gates.append({"id": gid, "label": label, "status": "not_run", "kind": "observed", "text": "not run"})
        elif "error" in res:
            gates.append({"id": gid, "label": label, "status": "fail", "kind": "observed", "text": f"failed: {res['error']}"})
        else:
            gates.append({"id": gid, "label": label, "status": status, "kind": "observed", "text": text})

    def st(rid: str) -> str:
        for r in decision["reasons"]:
            if r["id"] == rid:
                return "fail" if r["effect"] in ("reject", "cap_investigate") else "warn"
        return "pass"

    gate("permutation", "Permutation test", perm, st("permutation_p"), f"p = {fmt_num((perm or {}).get('p_value'), 4)} (n={ (perm or {}).get('n', 'no data') }, seed {(perm or {}).get('seed', 'no data')}); {(perm or {}).get('reading', '')}")
    wf_status = "fail" if any(r["id"] in ("wf_no_positive", "wf_few_positive", "wf_oos_nonpositive") and r["effect"] != "cap_review" for r in decision["reasons"]) else \
        "warn" if any(r["id"] in ("wf_few_positive", "wf_oos_unknown") for r in decision["reasons"]) else "pass"
    gate("walk_forward", "Walk-forward", wf, wf_status, f"{(wf or {}).get('positive_windows', 'no data')} of {(wf or {}).get('of_windows', 'no data')} out-of-sample windows positive; stitched out-of-sample Sharpe "
                                                    f"{fmt_num(((wf or {}).get('stitched_oos') or {}).get('sharpe'))}.")
    gate("bootstrap", "Bootstrap interval", boot, st("bootstrap_zero"), f"Sharpe {fmt_num((boot or {}).get('sharpe'))}, {int(((boot or {}).get('level') or 0) * 100)} % interval "
                                                                         f"[{fmt_num((boot or {}).get('ci_low'))}, {fmt_num((boot or {}).get('ci_high'))}]" + ("; includes zero" if (boot or {}).get("includes_zero") else ""))
    mt_status = "fail" if st("haircut_nonpositive") == "fail" else ("warn" if any(st(r) != "pass" for r in ("multiple_testing_p", "deflated")) else "pass")
    gate("multiple_testing", "Multiple testing", mt, mt_status, f"{(mt or {}).get('variants_tried', 'no data')} variant(s); Bonferroni-adjusted p = {fmt_num((mt or {}).get('p_value_bonferroni'), 4)}; "
                                                              f"haircut Sharpe {fmt_num((mt or {}).get('haircut_sharpe_annualised'))}.")
    if ho and ho.get("status") == "opened":
        gates.append({"id": "holdout", "label": "Holdout", "kind": "observed", "status": "fail" if st("holdout_failed") == "fail" or st("holdout_nonpositive") == "fail" else
                      ("warn" if st("holdout_peeked") != "pass" else "pass"), "text": f"opened (look #{ho.get('peek_number')}): return {fmt_pct((ho.get('metrics') or {}).get('total_return'))}, "
                                                                                     f"Sharpe {fmt_num((ho.get('metrics') or {}).get('sharpe'))}."})
    elif ho and ho.get("status") == "sealed":
        gates.append({"id": "holdout", "label": "Holdout", "kind": "observed", "status": "warn", "text": "sealed, untouched"})
    else:
        gates.append({"id": "holdout", "label": "Holdout", "kind": "observed", "status": "warn" if facts["has_validation"] else "not_run", "text": "none: no data was kept aside"})
    gates.append({"id": "duplicate", "label": "Duplicate risk", "kind": "observed", "status": {"high": "warn", "medium": "warn"}.get(dup.get("level"), "pass" if dup else "not_run"),
                  "text": (f"{dup.get('level')}" + (f": {len(dup.get('similar', []))} similar experiment(s) under other names" if dup.get("similar") else "")) if dup else "not computed"})

    # 4 metrics
    NA = object()  # "not applicable" (strategy-only row), as opposed to None = "no data"

    def row(name: str, strat: Any, bench: Any = NA, note: str = "", digits: int = 2, kind: str = "pct") -> dict[str, Any]:
        f = fmt_pct if kind == "pct" else fmt_num
        return {"metric": name, "strategy": strat if _ok(strat) else None, "benchmark": None if bench is NA or not _ok(bench) else bench,
                "strategy_text": f(strat, digits), "benchmark_text": "" if bench is NA else f(bench, digits), "note": note, "kind": "observed"}

    dev_rows = [row("total return", m.get("total_return"), b.get("total_return")), row("CAGR", m.get("cagr"), b.get("cagr"), "no data when the window is shorter than 3 months"),
                row("annualised volatility", m.get("ann_vol"), b.get("ann_vol")), row("Sharpe", m.get("sharpe"), b.get("sharpe"), kind="num"),
                row("Sortino", m.get("sortino"), NA, kind="num"), row("max drawdown", m.get("max_drawdown"), b.get("max_drawdown"), "negative fraction of the previous peak"),
                row("max drawdown duration (days)", m.get("max_drawdown_duration_days"), NA, "", 0, "num"), row("Calmar", m.get("calmar"), NA, kind="num"),
                row("hit rate (trades)", m.get("hit_rate"), NA, "no data when there are no trades", 1), row("trades", m.get("trades"), NA, "", 0, "num"),
                row("exposure (average gross)", m.get("exposure"), NA, "", 1), row("turnover per year", m.get("turnover_per_year"), NA, "sum of |change in weight| per year, not halved", kind="num"),
                row("costs paid", m.get("costs_paid"), NA, f"{fmt_pct(m.get('costs_pct_of_initial'))} of initial capital", 0, "num")]
    tables = [{"id": "development", "title": f"Development window {w.get('start')} to {w.get('end')} (in-sample)", "window": {"start": w.get("start"), "end": w.get("end")},
               "scope": "in_sample", "rows": dev_rows}]
    if wf and "error" not in wf and wf.get("stitched_oos"):
        s = wf["stitched_oos"]
        tables.append({"id": "walk_forward", "title": f"Walk-forward stitched out-of-sample {wf['windows'][0]['test_start']} to {wf['windows'][-1]['test_end']} "
                                                      f"({wf.get('of_windows')} windows)",
                       "window": {"start": wf["windows"][0]["test_start"], "end": wf["windows"][-1]["test_end"]}, "scope": "walk_forward_oos",
                       "rows": [row("bars", s.get("bars"), NA, "", 0, "num"), row("total return", s.get("total_return")), row("CAGR", s.get("cagr")), row("annualised volatility", s.get("ann_vol")),
                                row("Sharpe", s.get("sharpe"), NA, kind="num"), row("max drawdown", s.get("max_drawdown"))]})
    if ho and ho.get("status") == "opened":
        hm, hw = ho.get("metrics") or {}, ho.get("window") or {}
        tables.append({"id": "holdout", "title": f"Holdout {hw.get('start')} to {hw.get('end')} (out-of-sample, opened)", "window": {"start": hw.get("start"), "end": hw.get("end")},
                       "scope": "holdout", "rows": [row("total return", hm.get("total_return"), (ho.get("benchmark") or {}).get("total_return")), row("Sharpe", hm.get("sharpe"), NA, kind="num"),
                                                    row("max drawdown", hm.get("max_drawdown")), row("trades", hm.get("trades"), NA, "", 0, "num")]})
    conv = [claim("observed", CONVENTIONS[k]) for k in ("drawdown_sign", "turnover", "score", "nulls", "windows")]
    for wmsg in doc.get("warnings") or []:
        conv.append(claim("observed", f"Run warning: {wmsg}"))

    # 5 mechanism
    mech = [claim("observed", "Indicators: " + ("; ".join(describe_indicator(n, d) for n, d in (spec.get("indicators") or {}).items()) or "none (rules use raw fields)") + "."),
            claim("observed", f"Entry: {describe_rule(spec.get('entry'))}. Exit: {describe_rule(spec['exit']) if 'exit' in spec else 'the inverse of the entry'}. Sizing {(spec.get('sizing') or {}).get('type')}."),
            ]
    if spec.get("note"):
        mech.append(claim("observed", f"Stated by the author: {spec['note']}"))
    classes = mechanism_classes(spec)
    if classes:
        for c in classes:
            name, how = MECHANISM[c]
            mech.append(claim("inference", f"If the result is real, the likeliest mechanism is {name}: {how}. The backtest cannot prove it; it only shows the rule was profitable on this data."))
    else:
        mech.append(claim("inference", "The spec compares raw price fields only; it does not state a mechanism and none can be inferred."))
    beta, corr = b.get("beta"), b.get("correlation")
    if _ok(beta) and _ok(corr) and beta >= 0.7 and corr >= 0.7:
        mech.append(claim("alternative", f"The return may be market exposure rather than the rule: beta {beta:.2f}, correlation {corr:.2f} with {b.get('symbol')}, excess total return {fmt_pct(b.get('excess_total_return'))}."))
    elif _ok(beta):
        mech.append(claim("alternative", f"Market exposure looks limited (beta {beta:.2f}, correlation {fmt_num(corr)}), so a pure beta explanation is less likely; a different common factor is still possible."))
    else:
        mech.append(claim("alternative", "Market exposure cannot be assessed: beta or correlation has no data."))
    ntr = fam.get("effective", fam.get("tried"))
    mech.append(claim("alternative", f"The result may be selection luck: {ntr if ntr is not None else 'no data'} effective variant(s) tried"
                                      + (f", Bonferroni-adjusted p = {fmt_num((mt or {}).get('p_value_bonferroni'), 4)}" if mt and "error" not in mt else ", multiple testing not run") + "."))
    if wf and "error" not in wf and wf.get("windows"):
        pos_w = [r["window"] for r in wf["windows"] if _ok(r.get("oos_total_return")) and r["oos_total_return"] > 0]
        none_w = [r["window"] for r in wf["windows"] if not _ok(r.get("oos_total_return"))]
        mech.append(claim("alternative", f"The result may depend on a few periods: windows {', '.join(map(str, pos_w)) or 'none'} of {wf.get('of_windows')} were positive"
                                          + (f"; window(s) {', '.join(map(str, none_w))} have no data" if none_w else "") + "."))
    else:
        mech.append(claim("alternative", "Dependence on a particular period is untested (no walk-forward)."))
    mech.append(claim("alternative", f"The result may be sensitive to the cost assumption: turnover {fmt_num(m.get('turnover_per_year'))} per year, costs paid {fmt_pct(m.get('costs_pct_of_initial'))} of initial capital."))
    if hs.get("status") in ("warn", "fail"):
        mech.append(claim("alternative", "Part of the pattern may be a data artifact: " + "; ".join(i["issue"] for i in hs.get("issues", [])[:3]) + "."))

    # 6 risks and gaps
    risks = []
    for gt in gates:
        if gt["status"] == "not_run":
            risks.append(claim("observed", f"Evidence gap: {gt['label'].lower()} {gt['text']}."))
    for r in decision["reasons"]:
        risks.append(claim("observed", f"{r['text']}", rule=r["id"]))
    if fam.get("warning_level") in ("moderate", "strong"):
        risks.append(claim("observed", f"Multiple-testing warning level '{fam['warning_level']}': read every metric of this family as optimistic."))
    for s in (dup.get("similar") or [])[:3]:
        risks.append(claim("observed", f"Similar to {s['experiment']} '{s['name']}' (family {s['family']}, {s['level']}): " + "; ".join(s["why"]) + "."))
    risks.append(claim("observed", "Not modelled: financing of leverage and shorts, borrow fees, taxes, partial fills, market impact, cash interest."))
    risks.append(claim("inference", "A backtest of frozen history is not a forecast; a regime that is absent from the window is absent from the evidence."))

    # 7 next experiments
    nxt: list[dict[str, Any]] = []

    def add(text: str, why: str, tool: str) -> None:
        nxt.append({**claim("experiment", text), "priority": len(nxt) + 1, "why": why, "tool": tool})

    missing = [g["id"] for g in gates if g["id"] in ("permutation", "walk_forward", "bootstrap", "multiple_testing") and (g["status"] == "not_run" or g["text"].startswith("failed"))]
    if hs.get("status") in ("fail", "unknown"):
        add("Repair or replace the series that fail the data-health gates, then re-run the backtest.", "no metric can be interpreted until the data is trustworthy.", "data_health")
    if missing:
        add(f"Run the missing validations: {', '.join(missing)}.", "the decision cannot rise above 'investigate' without them.", "backtest_validate")
    if not split and not (ho and ho.get("status") == "opened"):
        add("Re-run the same spec with holdout_fraction=0.3, validate that run and open the holdout once.", "only data kept aside from the start counts as out-of-sample.", "backtest_run")
    elif ho and ho.get("status") == "sealed":
        add("Open the sealed holdout once, with the spec unchanged.", "adoption needs one honest out-of-sample look.", "backtest_validate")
    if dup.get("similar"):
        add("Compare with the similar experiments and merge or drop variants before trying more.", "near-duplicates inflate the real number of trials.", "experiments_duplicates")
    if _ok(m.get("turnover_per_year")) and m.get("turnover_per_year") > 20:
        add("Re-run with the cost and slippage assumptions doubled and compare.", "high turnover makes the result cost-sensitive (it counts as a new variant).", "backtest_run")
    if _ok(m.get("years")) and m["years"] < 5:
        add("Extend the history to include more than one market regime.", f"the window is {fmt_num(m.get('years'))} years.", "market_fetch")
    if _ok(beta) and beta >= 0.7:
        add("Compare against a benchmark scaled to the strategy's exposure to separate skill from beta.", "the strategy moves with its benchmark.", "backtest_run")
    if not nxt:
        add("Re-validate with a different seed and block size; the decision should not move.", "stability of the verdict under the random choices.", "backtest_validate")

    # 8 decision
    dec_claims = [claim("observed", f"Decision: {DECISION_LABELS[decision['decision']]['en']} ({DECISION_LABELS[decision['decision']]['es']}). {decision['meaning']}")]
    for r in decision["reasons"]:
        dec_claims.append(claim("observed", f"[{r['effect'].replace('cap_', 'caps at ').replace('reject', 'rejects')}] {r['text']}", rule=r["id"]))
    for s in decision["supports"]:
        dec_claims.append(claim("observed", f"[supports] {s['text']}", rule=s["id"]))
    dec_claims.append(claim("inference", "The decision is the outcome of fixed rules (see thresholds), not of a model or of a score; it can only change when the evidence does."))
    for n_ in decision["needs"]:
        dec_claims.append(claim("experiment", n_))

    content = {"identity": {"claims": ident}, "summary": {"claims": summ}, "gates": {"gates": gates, "claims": []}, "metrics": {"tables": tables, "claims": conv},
               "mechanism": {"claims": mech}, "risks": {"claims": risks}, "next": {"claims": nxt}, "decision": {"claims": dec_claims, "decision": decision}}
    return {"contract": {"version": 1, "sections": [s[0] for s in SECTIONS], "kinds": KIND_LABELS, "decisions": DECISION_LABELS, "conventions": CONVENTIONS},
            "run_id": doc["run_id"], "ref": doc.get("ref"), "title": f"Lab report {doc['run_id']} - {spec['name']}", "generated_at": doc.get("generated_at"),
            "sections": [{"id": sid, "number": i + 1, "title": en, "title_es": es, **content[sid]} for i, (sid, en, es) in enumerate(SECTIONS)],
            "decision": decision, "evidence_quality": quality, "data_health": health, "duplicate": dup or None, "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------------------------------------- rendering
def _tag(kind: str) -> str:
    return f"**[{KIND_LABELS[kind]['en']}]**"


def _cell(x: Any) -> str:
    return "" if x is None else str(x).replace("|", "\\|")


def to_markdown(rep: dict[str, Any], appendix: Optional[list[str]] = None) -> str:
    d = rep["decision"]
    lines = [f"# {rep['title']}", "", f"Reference: `{rep.get('ref')}` · generated {rep.get('generated_at') or ''} · decision **{DECISION_LABELS[d['decision']]['en']} ({DECISION_LABELS[d['decision']]['es']})**",
             "", f"> {rep['disclaimer']}", "", "Tags: " + " · ".join(f"**[{v['en']}]** {v['es']}" for v in KIND_LABELS.values()) + ".", ""]
    for sec in rep["sections"]:
        lines += [f"## {sec['number']}. {sec['title']}", ""]
        if sec.get("gates"):
            lines += [f"{_tag('observed')} Gates (pass / warn / fail / not_run):", "", "| gate | status | detail |", "|---|---|---|"]
            lines += [f"| {_cell(g['label'])} | {g['status']} | {_cell(g['text'])} |" for g in sec["gates"]] + [""]
        for t in sec.get("tables", []):
            lines += [f"{_tag('observed')} {t['title']}", "", "| metric | strategy | benchmark | note |", "|---|---|---|---|"]
            lines += [f"| {_cell(r['metric'])} | {_cell(r['strategy_text'])} | {_cell(r['benchmark_text'])} | {_cell(r['note'])} |" for r in t["rows"]] + [""]
        if sec["id"] == "next":
            lines += [f"{i + 1}. {_tag('experiment')} {c['text']} - {c['why']} (`{c['tool']}`)" for i, c in enumerate(sec["claims"])] + [""]
        else:
            lines += [f"- {_tag(c['kind'])} {c['text']}" for c in sec["claims"]] + ([""] if sec["claims"] else [])
    if appendix:
        lines += ["## Appendix", ""] + appendix
    return "\n".join(lines)


def to_html(rep: dict[str, Any], appendix_md: Optional[str] = None) -> str:
    e = html_mod.escape
    d = rep["decision"]
    out = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>" + e(rep["title"]) + "</title><style>",
           "body{font:14px/1.5 system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em;color:#222}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:3px 6px;text-align:left}",
           ".kind{display:inline-block;font-size:11px;font-weight:700;padding:0 6px;border-radius:9px;margin-right:6px;background:#eee}.kind-observed{background:#dde8ff}.kind-inference{background:#ffe9c2}",
           ".kind-alternative{background:#e6d9ff}.kind-experiment{background:#d5f2dd}.decision{font-size:16px;font-weight:700;padding:2px 10px;border-radius:12px;background:#eee}",
           ".decision-reject{background:#ffd5cf}.decision-investigate{background:#ffe9c2}.decision-review{background:#dde8ff}.decision-adopt{background:#d5f2dd}</style></head><body>",
           f"<h1>{e(rep['title'])}</h1>", f"<p>Decision: <span class=\"decision decision-{d['decision']}\">{e(DECISION_LABELS[d['decision']]['en'])} / {e(DECISION_LABELS[d['decision']]['es'])}</span></p>",
           f"<p><i>{e(rep['disclaimer'])}</i></p>"]

    def tag(kind: str) -> str:
        return f"<span class=\"kind kind-{kind}\">{e(KIND_LABELS[kind]['en'])}</span>"

    for sec in rep["sections"]:
        out.append(f"<h2>{sec['number']}. {e(sec['title'])}</h2>")
        if sec.get("gates"):
            out.append("<table><tr><th>gate</th><th>status</th><th>detail</th></tr>" + "".join(
                f"<tr><td>{e(g['label'])}</td><td>{e(g['status'])}</td><td>{tag('observed')}{e(g['text'])}</td></tr>" for g in sec["gates"]) + "</table>")
        for t in sec.get("tables", []):
            out.append(f"<p>{tag('observed')}{e(t['title'])}</p><table><tr><th>metric</th><th>strategy</th><th>benchmark</th><th>note</th></tr>" + "".join(
                f"<tr><td>{e(r['metric'])}</td><td>{e(r['strategy_text'])}</td><td>{e(r['benchmark_text'])}</td><td>{e(r['note'])}</td></tr>" for r in t["rows"]) + "</table>")
        out.append("<ul>" + "".join(f"<li>{tag(c['kind'])}{e(c['text'])}</li>" for c in sec["claims"]) + "</ul>")
    if appendix_md:
        out.append("<h2>Appendix</h2><pre>" + e(appendix_md) + "</pre>")
    out.append("</body></html>")
    return "".join(out)
