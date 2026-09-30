import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { BarList, Chip, ConfirmButton, Empty, ErrorBox, Field, Kpi, LineChart, Provenance } from "../components/ui.jsx";
import { SERIES_COLORS, money, num, pct, signClass } from "../format.js";

const blank = () => ({ symbol: "", quantity: "", snapshot_id: "", currency: "", cost_basis: "" });

function Editor({ snapshots, initial, onSaved }) {
  const { t, notify } = useApp();
  const [name, setName] = useState(initial?.name || "default");
  const [currency, setCurrency] = useState(initial?.currency || "EUR");
  const [rows, setRows] = useState(initial?.holdings?.length ? initial.holdings.map((h) => ({ ...blank(), ...h, quantity: String(h.quantity), cost_basis: h.cost_basis ?? "" })) : [blank()]);
  const [csv, setCsv] = useState("");
  const [error, setError] = useState(null);
  useEffect(() => {
    setName(initial?.name || "default");
    setCurrency(initial?.currency || "EUR");
    setRows(initial?.holdings?.length ? initial.holdings.map((h) => ({ ...blank(), ...h, quantity: String(h.quantity), cost_basis: h.cost_basis ?? "" })) : [blank()]);
  }, [initial]);
  const setRow = (i, k, v) => setRows(rows.map((r, n) => {
    if (n !== i) return r;
    const next = { ...r, [k]: v };
    if (k === "snapshot_id") {
      const s = snapshots.find((x) => x.id === v);
      if (s) { next.symbol = next.symbol || s.symbol; next.currency = next.currency || s.currency; }
    }
    return next;
  }));
  const save = async (e) => {
    e.preventDefault();
    try {
      const body = csv.trim()
        ? { csv_text: csv, currency }
        : { currency, holdings: rows.filter((r) => r.symbol && r.quantity !== "").map((r) => ({ symbol: r.symbol, quantity: Number(r.quantity), snapshot_id: r.snapshot_id || undefined, currency: r.currency || undefined, cost_basis: r.cost_basis === "" ? undefined : Number(r.cost_basis) })) };
      await api.portfolioSet(name, body);
      notify(`${t("saved")}: ${name}`);
      setError(null);
      setCsv("");
      onSaved(name);
    } catch (err) { setError(err); }
  };
  return (
    <form onSubmit={save} className="panel space-y-2">
      <h2>{t("holdings")}</h2>
      <div className="grid grid-cols-2 gap-2"><Field label={t("name")}><input className="field" required value={name} onChange={(e) => setName(e.target.value)} /></Field><Field label={t("base_currency")}><input className="field" maxLength={3} required value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></Field></div>
      <div className="overflow-x-auto">
        <table>
          <thead><tr><th>{t("symbol")}</th><th>{t("quantity")}</th><th>{t("snapshot")}</th><th>{t("currency")}</th><th>{t("cost_basis")}</th><th /></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>
              <td><input className="field" style={{ minWidth: 90 }} value={r.symbol} onChange={(e) => setRow(i, "symbol", e.target.value)} aria-label={t("symbol")} /></td>
              <td><input className="field" style={{ minWidth: 70 }} type="number" step="any" value={r.quantity} onChange={(e) => setRow(i, "quantity", e.target.value)} aria-label={t("quantity")} /></td>
              <td><select className="field" style={{ minWidth: 150 }} value={r.snapshot_id} onChange={(e) => setRow(i, "snapshot_id", e.target.value)} aria-label={t("snapshot")}><option value="">{t("latest_snapshot")}</option>{snapshots.map((s) => <option key={s.id} value={s.id}>{s.symbol} ({s.actual_end})</option>)}</select></td>
              <td><input className="field" style={{ width: 60 }} maxLength={3} value={r.currency} onChange={(e) => setRow(i, "currency", e.target.value.toUpperCase())} aria-label={t("currency")} /></td>
              <td><input className="field" style={{ width: 80 }} type="number" step="any" value={r.cost_basis} onChange={(e) => setRow(i, "cost_basis", e.target.value)} aria-label={t("cost_basis")} /></td>
              <td><button type="button" className="btn btn-sm" onClick={() => setRows(rows.filter((_, n) => n !== i))} aria-label={t("remove")}>×</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <button type="button" className="btn btn-sm" onClick={() => setRows([...rows, blank()])}>+ {t("add_row")}</button>
      <Field label="CSV" hint={t("portfolio_csv_hint")}><textarea className="field" rows={3} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder="symbol,quantity,snapshot_id,currency,cost_basis" /></Field>
      <ErrorBox error={error} t={t} />
      <button className="btn btn-primary">{t("save")}</button>
    </form>
  );
}

function Analysis({ name, snapshots, holdings, base }) {
  const { t, lang } = useApp();
  const [date, setDate] = useState("");
  const [currency, setCurrency] = useState("");
  const [fx, setFx] = useState({});
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const foreign = Array.from(new Set((holdings || []).map((h) => h.currency).filter((c) => c && c !== (currency || base))));

  const run = useCallback(async () => {
    setBusy(true);
    try {
      const chosen = Object.fromEntries(Object.entries(fx).filter(([, v]) => v));
      setData(await api.portfolioAnalyze(name, { date: date || undefined, currency: currency || undefined, fx: Object.keys(chosen).length ? chosen : undefined }));
      setError(null);
    } catch (e) { setData(null); setError(e); } finally { setBusy(false); }
  }, [name, date, currency, fx]);
  useEffect(() => { setData(null); setReport(null); }, [name]);

  return (
    <div className="space-y-3">
      <div className="panel flex flex-wrap items-end gap-3">
        <label className="block w-[150px]"><span className="label">{t("valuation_date")}</span><input className="field" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="block w-[90px]"><span className="label">{t("currency")}</span><input className="field" maxLength={3} placeholder={base} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
        {foreign.map((c) => (
          <label key={c} className="block w-[210px]"><span className="label">{t("fx_for")} {c}</span>
            <select className="field" value={fx[c] || ""} onChange={(e) => setFx({ ...fx, [c]: e.target.value })}><option value="">{t("auto")}</option>{snapshots.filter((s) => /per|fx/i.test(s.unit || "") || /^fakefx|eur|usd/i.test(s.symbol)).map((s) => <option key={s.id} value={s.id}>{s.symbol} ({s.unit})</option>)}</select>
          </label>
        ))}
        <button type="button" className="btn btn-primary" onClick={run} disabled={busy}>{busy ? "…" : t("analyze")}</button>
        {data && <button type="button" className="btn" onClick={async () => setReport(await api.report({ kind: "portfolio", id: name }))}>{t("report")}</button>}
      </div>
      <ErrorBox error={error} t={t} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label={t("total_value")} value={money(data.total_value, data.currency, lang)} />
            <Kpi label={t("valuation_date")} value={data.valuation_date} />
            <Kpi label={t("volatility")} value={pct(data.risk?.ann_vol, 1, lang)} />
            <Kpi label={t("max_drawdown")} value={pct(data.risk?.max_drawdown, 1, lang)} tone="neg" />
            <Kpi label={t("top_weight")} value={pct(data.concentration?.top_weight, 0, lang)} />
            <Kpi label={t("effective_holdings")} value={num(data.concentration?.effective_holdings, 1, lang)} />
          </div>
          {data.warnings?.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
          <div className="panel">
            <h3>{t("value_history")}</h3>
            <LineChart series={[{ key: "v", name: t("total_value"), color: SERIES_COLORS[0], points: data.points.map((p) => [p.date, p.value]) }]} yFormat={(v) => money(v, data.currency, lang)} />
            <p className="help m-0">{data.risk?.note} {data.risk?.start} → {data.risk?.end}</p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="panel space-y-2"><h3>{t("allocation")}</h3><BarList items={data.allocation.map((a, i) => ({ label: a.symbol, value: a.weight, color: SERIES_COLORS[i % SERIES_COLORS.length] }))} format={(v) => pct(v, 1, lang)} />
              <h3>{t("currency_exposure")}</h3><BarList items={Object.entries(data.currency_exposure).map(([k, v]) => ({ label: k, value: v }))} format={(v) => pct(v, 1, lang)} /></div>
            <div className="panel overflow-x-auto"><h3 className="mb-1">{t("correlation")}</h3>
              <table className="num"><thead><tr><th />{Object.keys(data.risk.correlation).map((k) => <th key={k} className="r">{k}</th>)}</tr></thead>
                <tbody>{Object.entries(data.risk.correlation).map(([a, row]) => <tr key={a}><th>{a}</th>{Object.keys(row).map((b) => <td key={b} className="r">{num(row[b], 2, lang)}</td>)}</tr>)}</tbody></table>
              <p className="help m-0">{t("risk_total_return")}: <span className={signClass(data.risk.total_return_same_quantities)}>{pct(data.risk.total_return_same_quantities, 1, lang, true)}</span></p>
            </div>
          </div>
          <div className="panel overflow-x-auto">
            <h3 className="mb-1">{t("holdings")}</h3>
            <table className="num">
              <thead><tr><th>{t("symbol")}</th><th className="r">{t("quantity")}</th><th>{t("price_date")}</th><th className="r">{t("price")}</th><th className="r">{t("value")}</th><th className="r">{t("weight")}</th><th className="r">{t("unrealised")}</th></tr></thead>
              <tbody>{data.holdings.map((h) => {
                const pl = h.cost_basis ? h.price_native / h.cost_basis - 1 : null;
                return <tr key={h.symbol}><td>{h.symbol} <span className="help">{h.currency}</span></td><td className="r">{num(h.quantity, 2, lang)}</td><td className="mono">{h.price_date}</td><td className="r">{num(h.price_native, 2, lang)}{h.price_base !== h.price_native ? <span className="help block">{num(h.price_base, 2, lang)} {data.currency}</span> : null}</td><td className="r">{money(h.value_base, data.currency, lang)}</td><td className="r">{pct(h.weight, 1, lang)}</td><td className={`r ${signClass(pl)}`}>{pl === null ? "—" : pct(pl, 1, lang, true)}</td></tr>;
              })}</tbody>
            </table>
            {data.conversions?.map((c, i) => <p key={i} className="help m-0">{c.holding}: {c.from}→{c.to} · {c.fx_symbol} ({c.pair}, {c.direction}){c.auto ? ` · ${t("auto_selected")}` : ""}</p>)}
          </div>
          <div className="flex flex-wrap gap-2">{data.snapshots.map((s) => <Provenance key={s.id} snap={s} t={t} />)}</div>
        </>
      )}
      {report && <div className="panel"><pre className="mono m-0 max-h-[320px] overflow-auto whitespace-pre-wrap">{report.markdown}</pre></div>}
    </div>
  );
}

export default function Portfolio() {
  const { t } = useApp();
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([api.portfolios(), api.snapshots({ limit: 100 })]);
      setList(p.portfolios);
      setSnapshots(s.snapshots);
      setError(null);
    } catch (e) { setError(e); }
  }, []);
  useEffect(() => { load(); }, [load, version]);
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    api.portfolio(selected).then((d) => setDetail({ name: d.name, currency: d.currency, holdings: d.holdings })).catch((e) => { setDetail({ name: selected, currency: "EUR", holdings: [] }); setError(e); });
  }, [selected, version]);
  useEffect(() => { if (!selected && list.length) setSelected(list[0].name); }, [list, selected]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1>{t("nav_portfolio")}</h1><button type="button" className="btn" onClick={() => { setSelected(null); setDetail(null); }}>{t("new_portfolio")}</button></div>
      <ErrorBox error={error} t={t} />
      <div className="flex flex-wrap gap-2">
        {list.map((p) => (
          <span key={p.name} className="inline-flex items-center gap-1">
            <button type="button" className={`btn btn-sm ${selected === p.name ? "btn-primary" : ""}`} onClick={() => setSelected(p.name)}>{p.name} <span className="opacity-70">({p.holdings})</span></button>
          </span>
        ))}
        {selected && <ConfirmButton t={t} onConfirm={async () => { await api.portfolioDelete(selected); setSelected(null); setVersion((v) => v + 1); }} />}
      </div>
      {!selected && list.length === 0 && <Empty>{t("portfolio_empty")}</Empty>}
      <div className="grid gap-4 xl:grid-cols-[600px_minmax(0,1fr)]">
        <Editor snapshots={snapshots} initial={selected ? detail : null} onSaved={(n) => { setSelected(n); setVersion((v) => v + 1); }} />
        <div className="min-w-0">{selected ? <Analysis name={selected} snapshots={snapshots} holdings={detail?.holdings} base={detail?.currency || "EUR"} /> : null}</div>
      </div>
    </div>
  );
}
