"""Markdown (and JSON) reports: every number with the snapshot, provider and dates it comes from."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

DISCLAIMER = "Results are historical analyses, not advice. Past performance does not predict future results."
LIMITS = ("Not modelled: financing of leverage and shorts, borrow fees, taxes, partial fills, market impact, cash interest. "
          "Costs are basis points charged on every change of target weight.")


def pct(x: Any, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def num(x: Any, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if c is None else str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def provenance_table(snaps: list[dict[str, Any]]) -> str:
    rows = [[s["id"], s["provider"], s["symbol"], s.get("fetched_at", ""), f"{s.get('actual_start', '')} to {s.get('actual_end', '')}",
             s.get("frequency", ""), s.get("currency") or "-", "adjusted" if s.get("adjusted") else "raw", s.get("unit", ""), s.get("rows", ""),
             (s.get("sha256") or "")[:16]] for s in snaps]
    return _table(["snapshot", "provider", "symbol", "fetched (UTC)", "range", "freq", "currency", "prices", "unit", "rows", "sha256"], rows)


def terms_block(snaps: list[dict[str, Any]]) -> str:
    seen, lines = set(), []
    for s in snaps:
        if s["provider"] in seen:
            continue
        seen.add(s["provider"])
        lines.append(f"- **{s['provider']}** — {s.get('terms', '')} Delay: {s.get('delay', '')}")
    return "\n".join(lines)


def run_markdown(d: dict[str, Any]) -> str:
    m, b, w, spec = d["metrics"], d["benchmark"], d["window"], d["spec"]
    v, mt = d["variants"], d.get("multiple_testing") or {}
    lines = [f"# Backtest {d['run_id']} — {spec['name']}", "",
             f"Reference: `{d['ref']}` · spec hash `{d['spec_hash']}` · window {w['start']} to {w['end']} ({w['bars']} bars) · generated {d.get('generated_at', '')}", "",
             f"> {DISCLAIMER}", "", "## Headline", "",
             _table(["metric", "strategy", f"benchmark ({b.get('symbol')})"], [
                 ["total return", pct(m["total_return"]), pct(b.get("total_return"))], ["CAGR", pct(m["cagr"]), pct(b.get("cagr"))],
                 ["annualised volatility", pct(m["ann_vol"]), pct(b.get("ann_vol"))], ["Sharpe", num(m["sharpe"]), num(b.get("sharpe"))],
                 ["Sortino", num(m["sortino"]), ""], ["max drawdown", pct(m["max_drawdown"]), pct(b.get("max_drawdown"))],
                 ["max drawdown duration", f"{m['max_drawdown_duration_days']} days ({m['max_drawdown_duration_bars']} bars)", ""],
                 ["Calmar", num(m["calmar"]), ""], ["hit rate (trades)", pct(m["hit_rate"]), ""], ["trades", m["trades"], ""],
                 ["exposure (avg gross)", pct(m["exposure"]), ""], ["turnover per year", num(m["turnover_per_year"]), ""],
                 ["costs paid", f"{num(m['costs_paid'])} ({pct(m['costs_pct_of_initial'])} of initial capital)", ""]]),
             "", f"Risk-free: {m['rf']['note']}. Beta vs benchmark {num(b.get('beta'))}, correlation {num(b.get('correlation'))}, excess total return {pct(b.get('excess_total_return'))}.", "",
             "## How many things were tried", "",
             f"**{v['tried']} variant(s)** of the family `{v['family']}` are in the experiments log (warning level: {v['warning_level']})."]
    if mt.get("warning"):
        lines += ["", f"**Warning:** {mt['warning']}"]
    if mt.get("p_value") is not None:
        hs = mt.get("haircut_sharpe_annualised")
        lines += ["", f"- Sharpe t-statistic {num(mt.get('t_stat'))}, p = {num(mt.get('p_value'), 4)}; Bonferroni-adjusted p = {num(mt.get('p_value_bonferroni'), 4)} over {mt['variants_tried']} variant(s)",
                  f"- Haircut Sharpe (annualised): {num(hs)}"]
        dsr = mt.get("deflated_sharpe")
        if dsr:
            lines.append(f"- Deflated Sharpe probability: {pct(dsr['probability'], 1)} (expected best of {dsr['trials']} trials: Sharpe {num(dsr['expected_max_sharpe_annualised'])})")
    lines += ["", "Methods: " + "; ".join(mt.get("methods", [])), ""]
    if d.get("holdout"):
        h = d["holdout"]
        lines += ["## Holdout", "", f"Sealed. This run used data up to {h['dev_end']}; {h['holdout_bars']} bars from {h['holdout_start']} to {h['holdout_end']} were not used. "
                  "Open it with backtest_validate(reveal_holdout=true) once the spec is final.", ""]
    lines += ["## Spec", "", "```json", __import__("json").dumps(spec, indent=2, sort_keys=True), "```", ""]
    if d.get("conversions"):
        lines += ["## Currency conversions", "", _table(["asset", "from", "to", "pair", "direction", "fx snapshot", "max rate staleness (days)"],
                                                        [[k, c["from"], c["to"], c["pair"], c["direction"], c["fx_snapshot"], c.get("max_rate_staleness_days")] for k, c in d["conversions"].items()]), ""]
    lines += ["## Data provenance", "", provenance_table(d["snapshots"]), "", "Provider terms:", terms_block(d["snapshots"]), ""]
    if d.get("warnings"):
        lines += ["## Warnings", ""] + [f"- {x}" for x in d["warnings"]] + [""]
    trades = d.get("trades") or []
    if trades:
        lines += ["## Trades (first 20)", "", _table(["asset", "side", "entry", "exit", "return", "bars"],
                                                      [[t["asset"], t["side"], f"{t['entry_date']} @ {num(t['entry_price'])}", f"{t['exit_date']} @ {num(t['exit_price'])}" + (" (open)" if t["open"] else ""),
                                                        pct(t["return"]), t["bars"]] for t in trades[:20]]), ""]
    lines += ["## Limits", "", LIMITS, ""]
    return "\n".join(lines)


def thesis_markdown(t: dict[str, Any], *, pack: Optional[dict[str, Any]] = None, committee: Optional[dict[str, Any]] = None, snapshots: Optional[list[dict[str, Any]]] = None) -> str:
    lines = [f"# Thesis {t['id']} — {t['title']}", "", f"Reference: `{t['ref']}` · status **{t['status']}** · as of {t['as_of']} · horizon {t['horizon'] or 'open'}"
             + (f" (ends {t['horizon_end']})" if t.get("horizon_end") else ""), "", f"> {DISCLAIMER}", "", "## Claim", "", t["claim"], "",
             "## Rival hypothesis", "", t["rival"], ""]
    if t["assumptions"]:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in t["assumptions"]] + [""]
    for side, title in (("for", "Evidence for"), ("against", "Evidence against")):
        items = [e for e in t["evidence"] if e["side"] == side]
        lines += [f"## {title}", ""]
        if not items:
            lines += ["_None recorded._", ""]
        for e in items:
            extra = ""
            if e["kind"] == "snapshot_metric":
                mm = e["metric"]
                extra = f" — `{mm.get('expr')}` = {mm.get('value'):.6g} as of {mm.get('as_of')} (snapshot {e['ref']}, data to {', '.join(mm.get('data_dates', {}).values()) or 'n/a'})"
            elif e["ref"]:
                extra = f" — {e['ref']}"
            quote = f" “{e['quote']}”" if e["quote"] else ""
            lines.append(f"- **{e['label']}** ({e['kind']}) {e['title']}{extra}{quote}")
        lines.append("")
    lines += ["## Invalidation rules", ""]
    if not t["rules"]:
        lines += ["_No rules._", ""]
    for r in t["rules"]:
        last = r.get("last") or {}
        lines.append(f"- `{r['id']}` `{r['expr']}` — last state **{last.get('state', 'unchecked')}**" + (f" at {last['as_of']}" if last.get("as_of") else "")
                     + (f" — {r['description']}" if r.get("description") else ""))
        for term in last.get("terms", []):
            lines.append(f"    - {term['expr']}: left {term.get('lhs')}, right {term.get('rhs')} → {term.get('value')}")
    lc = t.get("last_check")
    if lc:
        lines += ["", f"Last check {lc['id']} at {lc['as_of']}: {'a rule tripped' if lc['tripped'] else 'no rule tripped'}; status {lc['status_before']} → {lc['status_after']}."]
        for w in lc.get("warnings", []):
            lines.append(f"- warning: {w}")
    if pack:
        lines += ["", "## Assets (cut at the thesis date)", ""]
        rows = [[a["symbol"], a["snapshot"]["id"], num(a["metrics"].get("last"), 4), a["metrics"].get("last_date"), pct(a["metrics"].get("trailing_1y_return")),
                 pct(a["metrics"].get("trailing_1y_vol")), pct(a["metrics"].get("trailing_1y_max_drawdown"))] for a in pack["assets"]]
        lines += [_table(["symbol", "snapshot", "last", "date", "1y return", "1y volatility", "1y max drawdown"], rows) if rows else "_No asset data._"]
        if pack["missing_data"]:
            lines += ["", "Missing data: " + ", ".join(pack["missing_data"])]
    if committee:
        lines += ["", "## Committee", "", f"Mode: {committee['mode']}" + (f" · model {committee.get('model')}" if committee.get("model") else "")]
        if committee["mode"] == "model":
            lines += [f"Verdict: **{committee['verdict']}** (confidence {committee['confidence']}/100) — {committee['method']}", ""]
            for title, key in (("Advocate for", "advocate_for"), ("Advocate against", "advocate_against"), ("Risk review", "risk_review")):
                lines += [f"### {title}", ""] + [f"- {i['text']} {('[' + ', '.join(i['evidence']) + ']') if i['evidence'] else '(uncited)'}" for i in committee.get(key, [])] + [""]
            arb = committee["arbiter"]
            lines += ["### Arbiter", ""] + [f"- {i['text']} [{', '.join(i['evidence'])}]" for i in arb["reasons"]]
            if arb["missing_evidence"]:
                lines += ["", "Missing evidence: " + "; ".join(arb["missing_evidence"])]
            led = committee["ledger"]
            lines += ["", f"Number ledger: {'all numbers accounted for' if led['ok'] else str(len(led['unmatched'])) + ' unmatched number(s): ' + ', '.join(u['section'] + ' ' + u['text'] for u in led['unmatched'])}."]
        else:
            lines += [f"No model verdict: {committee.get('reason')}.", "", f"Deterministic summary: {committee['deterministic']}"]
    if snapshots:
        lines += ["", "## Data provenance", "", provenance_table(snapshots), "", "Provider terms:", terms_block(snapshots)]
    lines += ["", "## Limits", "", "Evidence metrics are computed on data cut at the thesis date; checks use the data available at the check date. "
              "Macro series are as last revised (not vintages). " + DISCLAIMER, ""]
    return "\n".join(lines)


def portfolio_markdown(a: dict[str, Any]) -> str:
    lines = [f"# Portfolio {a['portfolio']}", "", f"Valuation date {a['valuation_date']} · base currency {a['currency']} · total {num(a['total_value'])}", "", f"> {a['disclaimer']}", "",
             "## Holdings", "", _table(["symbol", "qty", "ccy", "price date", "price", "value", "weight", "unrealised P&L"],
                                        [[h["symbol"], h["quantity"], h["currency"], h["price_date"], num(h["price_native"], 4), num(h["value_base"]), pct(h["weight"]),
                                          pct(h.get("unrealised_pnl_pct")) if h.get("unrealised_pnl_pct") is not None else ""] for h in a["holdings"]]), "",
             "## Exposure", "", "Currency: " + ", ".join(f"{k} {pct(v, 1)}" for k, v in a["currency_exposure"].items()),
             f"Concentration: top weight {pct(a['concentration']['top_weight'], 1)}, effective number of holdings {num(a['concentration']['effective_holdings'])}.", ""]
    r = a["risk"]
    if r.get("ann_vol") is not None:
        lines += ["## Risk (constant quantities over the shared history)", "",
                  f"{r['start']} to {r['end']} ({r['observations']} observations): annualised volatility {pct(r['ann_vol'])}, max drawdown {pct(r['max_drawdown'])}, current drawdown {pct(r['current_drawdown'])}.", ""]
        if r.get("risk_contribution"):
            lines += ["Risk contribution: " + ", ".join(f"{k} {pct(v, 1)}" for k, v in r["risk_contribution"].items()), ""]
    else:
        lines += ["## Risk", "", r.get("note", ""), ""]
    if a["conversions"]:
        lines += ["## Currency conversions", "", _table(["holding", "from", "to", "pair", "direction", "fx snapshot", "auto"],
                                                        [[c["holding"], c["from"], c["to"], c["pair"], c["direction"], c["fx_snapshot"], c["auto"]] for c in a["conversions"]]), ""]
    lines += ["## Data provenance", "", provenance_table(a["snapshots"]), "", "Provider terms:", terms_block(a["snapshots"]), ""]
    if a["warnings"]:
        lines += ["## Warnings", ""] + [f"- {x}" for x in a["warnings"]] + [""]
    return "\n".join(lines)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
