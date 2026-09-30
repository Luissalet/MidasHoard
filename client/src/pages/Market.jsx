import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { BarList, Empty, ErrorBox, Field, Kpi, LineChart, Legend, Provenance, Tabs } from "../components/ui.jsx";
import { SERIES_COLORS, num, pct, signClass } from "../format.js";

const ymd = (d) => d.toISOString().slice(0, 10);

function SeriesView({ snap, all }) {
  const { t, lang } = useApp();
  const [mode, setMode] = useState("price");
  const [resample, setResample] = useState("auto");
  const [asOf, setAsOf] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const auto = resample === "auto" ? (snap.frequency === "D" && snap.rows > 900 ? "W" : undefined) : resample || undefined;
    setBusy(true);
    api.series({
      snapshot_id: snap.id, limit: 1000, cursor: 0, as_of: asOf || undefined, resample: auto,
      rebase: mode === "rebase", drawdown: mode === "drawdown", vol_window: mode === "vol" ? (auto === "W" ? 26 : 30) : undefined,
    })
      .then((d) => { if (live) { setData({ ...d, resampled: auto }); setError(null); } })
      .catch((e) => { if (live) setError(e); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [snap.id, mode, resample, asOf]);

  const column = { price: "value", rebase: "rebased", drawdown: "drawdown_pct", vol: data ? data.columns.find((c) => c.startsWith("vol_")) : "vol" }[mode];
  const points = useMemo(() => (data ? data.points.map((p) => [p.date, p[column]]) : []), [data, column]);
  const s = data?.summary;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2>{snap.symbol}</h2>
        <Provenance snap={snap} t={t} />
        <span className="chip">{snap.currency || snap.unit || t("no_unit")}</span>
        {snap.adjusted ? <span className="chip">{t("adjusted")}</span> : <span className="chip chip-amber">{t("not_adjusted")}</span>}
        {data?.resampled && <span className="chip">{t("resampled")} {data.resampled}</span>}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex gap-1">
          {["price", "rebase", "drawdown", "vol"].map((m) => (
            <button key={m} type="button" className={`btn btn-sm ${mode === m ? "btn-primary" : ""}`} onClick={() => setMode(m)}>{t(`mode_${m}`)}</button>
          ))}
        </div>
        <label className="block w-[110px]">
          <span className="label">{t("resample")}</span>
          <select className="field" value={resample} onChange={(e) => setResample(e.target.value)}>
            <option value="auto">{t("auto")}</option>
            <option value="">{t("none")}</option>
            <option value="W">{t("weekly")}</option><option value="M">{t("monthly")}</option><option value="Q">{t("quarterly")}</option><option value="A">{t("yearly")}</option>
          </select>
        </label>
        <label className="block w-[140px]">
          <span className="label">{t("as_of")}</span>
          <input className="field" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
      </div>
      <ErrorBox error={error} t={t} />
      {s && (
        <div className="kpis">
          <Kpi label={t("last")} value={`${num(s.last, 2, lang)}`} />
          <Kpi label={t("total_return")} value={pct(s.total_return, 1, lang, true)} tone={signClass(s.total_return)} />
          <Kpi label="CAGR" value={pct(s.cagr, 1, lang, true)} tone={signClass(s.cagr)} />
          <Kpi label={t("volatility")} value={pct(s.ann_vol, 1, lang)} />
          <Kpi label={t("max_drawdown")} value={pct(s.max_drawdown, 1, lang)} tone="neg" />
          <Kpi label={t("current_drawdown")} value={pct(s.current_drawdown, 1, lang)} tone={signClass(s.current_drawdown)} />
        </div>
      )}
      <div className="panel" style={{ opacity: busy ? 0.6 : 1 }}>
        <LineChart series={[{ key: snap.id, name: snap.symbol, color: SERIES_COLORS[0], points }]} fill={mode === "drawdown"} label={snap.symbol}
          yFormat={(v) => num(v, mode === "drawdown" || mode === "vol" ? 1 : 2, lang)} />
        <p className="help mt-1">{mode === "drawdown" ? t("unit_pct_below_peak") : mode === "vol" ? t("unit_vol") : ""} {s && `${s.first_date} → ${s.last_date} · ${s.n} ${t("observations")}`}</p>
      </div>
      {data?.warnings?.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
      <p className="help">{snap.terms}</p>
    </div>
  );
}

function CompareView({ snaps, all }) {
  const { t, lang } = useApp();
  const [allow, setAllow] = useState(false);
  const [convertTo, setConvertTo] = useState("");
  const [fx, setFx] = useState("");
  const [asOf, setAsOf] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const ids = snaps.map((s) => s.id).join(",");

  useEffect(() => {
    let live = true;
    api.compare({ snapshot_ids: snaps.map((s) => s.id), allow_incompatible: allow, convert_to: convertTo || undefined, fx: fx || undefined, as_of: asOf || undefined, limit: 300 })
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) { setData(null); setError(e); } });
    return () => { live = false; };
  }, [ids, allow, convertTo, fx, asOf]);

  const series = data ? data.series.map((name, i) => ({ key: name, name, color: SERIES_COLORS[i % SERIES_COLORS.length], points: data.points.map((p) => [p.date, p[name]]) })) : [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><h2>{t("compare")}</h2>{snaps.map((s) => <Provenance key={s.id} snap={s} t={t} />)}</div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block w-[110px]"><span className="label">{t("convert_to")}</span><input className="field" maxLength={3} placeholder="EUR" value={convertTo} onChange={(e) => setConvertTo(e.target.value.toUpperCase())} /></label>
        <label className="block w-[230px]"><span className="label">{t("fx_snapshot")}</span>
          <select className="field" value={fx} onChange={(e) => setFx(e.target.value)}>
            <option value="">—</option>
            {all.map((s) => <option key={s.id} value={s.id}>{s.symbol} ({s.unit || s.currency})</option>)}
          </select>
        </label>
        <label className="block w-[140px]"><span className="label">{t("as_of")}</span><input className="field" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <label className="flex items-center gap-2 pb-1 text-[12.5px]"><input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} />{t("allow_incompatible")}</label>
      </div>
      <ErrorBox error={error} t={t} />
      {data && (
        <>
          <div className="panel">
            <Legend items={series.map((s) => ({ name: s.name, color: s.color }))} />
            <LineChart series={series} yFormat={(v) => num(v, 1, lang)} label={t("compare")} />
            <p className="help">{t("rebased_100")} · {data.common_range.start} → {data.common_range.end} · {data.common_range.rows} {t("observations")}</p>
          </div>
          {data.warnings?.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="panel overflow-x-auto">
              <h3 className="mb-2">{t("correlation")}</h3>
              <table className="num"><thead><tr><th />{data.series.map((n) => <th key={n} className="r">{n}</th>)}</tr></thead>
                <tbody>{data.series.map((a) => (
                  <tr key={a}><th>{a}</th>{data.series.map((b) => <td key={b} className="r">{num(data.correlation[a][b], 2, lang)}</td>)}</tr>
                ))}</tbody></table>
              <p className="help mt-1">{data.correlation_basis}</p>
            </div>
            <div className="panel overflow-x-auto">
              <h3 className="mb-2">{t("statistics")}</h3>
              <table className="num"><thead><tr><th />{["total_return", "cagr", "volatility", "max_drawdown"].map((k) => <th key={k} className="r">{t(k)}</th>)}</tr></thead>
                <tbody>{data.series.map((n) => {
                  const s = data.stats[n];
                  return <tr key={n}><th>{n}</th><td className="r">{pct(s.total_return, 1, lang, true)}</td><td className="r">{pct(s.cagr, 1, lang, true)}</td><td className="r">{pct(s.ann_vol, 1, lang)}</td><td className="r">{pct(s.max_drawdown, 1, lang)}</td></tr>;
                })}</tbody></table>
              <div className="mt-3"><BarList items={data.series.map((n, i) => ({ label: n, value: data.relative_performance[n], color: SERIES_COLORS[i % SERIES_COLORS.length] }))} format={(v) => num(v, 0, lang)} /></div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function Market() {
  const { t, lang, notify, refresh } = useApp();
  const [providers, setProviders] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [selected, setSelected] = useState([]);
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("search");
  const [form, setForm] = useState({ provider: "fake", symbol: "", start: "", end: "", interval: "d", currency: "" });
  const [csv, setCsv] = useState({ symbol: "", currency: "", unit: "", text: "", dayfirst: false, delimiter: "", decimal: "" });
  const [fetchNotes, setFetchNotes] = useState([]);

  const loadSnaps = useCallback(async () => {
    try { setSnapshots((await api.snapshots({ limit: 100 })).snapshots); } catch (e) { setError(e); }
  }, []);
  useEffect(() => {
    loadSnaps();
    api.providers().then((p) => setProviders(p.providers)).catch(() => {});
  }, [loadSnaps]);

  const search = async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    try { setResults(await api.search({ q: query, remote: remote || undefined })); setError(null); } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const doFetch = async (body) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.fetchSeries(body);
      setFetchNotes([res.note, ...(res.warnings || [])].filter(Boolean));
      notify(`${res.snapshot.symbol}: ${res.snapshot.rows} ${t("rows")}${res.reused_existing ? ` (${t("reused")})` : ""}`);
      await loadSnaps();
      await refresh();
      setSelected([res.snapshot.id]);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const fetchResult = (r) => doFetch({ provider: r.provider, symbol: r.symbol, start: form.start || undefined, end: form.end || undefined, interval: form.interval, currency: form.currency || undefined });
  const fetchManual = (e) => { e.preventDefault(); doFetch({ provider: form.provider, symbol: form.symbol, start: form.start || undefined, end: form.end || undefined, interval: form.interval, currency: form.currency || undefined }); };
  const importCsv = (e) => {
    e.preventDefault();
    doFetch({ provider: "csv", symbol: csv.symbol, csv_text: csv.text, currency: csv.currency || undefined, unit: csv.unit || undefined, dayfirst: csv.dayfirst, delimiter: csv.delimiter || undefined, decimal: csv.decimal || undefined });
  };

  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-12)));
  const chosen = snapshots.filter((s) => selected.includes(s.id));
  const today = ymd(new Date());

  return (
    <div className="space-y-4">
      <h1>{t("nav_market")}</h1>
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-3">
          <Tabs tabs={[{ key: "search", label: t("search") }, { key: "manual", label: t("manual") }, { key: "csv", label: "CSV" }]} active={tab} onChange={setTab} />
          <div className="panel space-y-2">
            {tab === "search" && (
              <>
                <form onSubmit={search} className="flex gap-2">
                  <input className="field" placeholder={t("search_placeholder")} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t("search")} />
                  <button className="btn btn-primary" disabled={busy}>{t("search")}</button>
                </form>
                <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} />{t("search_remote")}</label>
                <div className="grid grid-cols-2 gap-2">
                  <Field label={t("start")}><input className="field" type="date" max={today} value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></Field>
                  <Field label={t("end")}><input className="field" type="date" max={today} value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></Field>
                </div>
                {results && (results.results.length ? (
                  <ul className="m-0 max-h-[320px] list-none space-y-1 overflow-y-auto p-0">
                    {results.results.map((r) => (
                      <li key={`${r.provider}:${r.symbol}`} className="flex items-center justify-between gap-2 rounded px-1 py-1 hover:bg-white/5">
                        <div className="min-w-0"><div className="truncate font-semibold">{r.symbol} <span className="help">{r.provider}</span></div><div className="help truncate">{r.name} · {r.currency || r.unit || "—"} · {r.frequency}{r.snapshots ? ` · ${r.snapshots} ${t("count_snapshots")}` : ""}</div></div>
                        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => fetchResult(r)}>{t("fetch")}</button>
                      </li>
                    ))}
                  </ul>
                ) : <p className="help">{t("no_results")}</p>)}
                {results?.notes?.map((n, i) => <p key={i} className="help">{n}</p>)}
              </>
            )}
            {tab === "manual" && (
              <form onSubmit={fetchManual} className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <Field label={t("provider")}><select className="field" value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>{(providers.length ? providers : [{ id: "fake" }]).filter((p) => p.id !== "csv").map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}</select></Field>
                  <Field label={t("interval")}><select className="field" value={form.interval} onChange={(e) => setForm({ ...form, interval: e.target.value })}>{["d", "w", "m", "q", "y"].map((i) => <option key={i} value={i}>{i}</option>)}</select></Field>
                </div>
                <Field label={t("symbol")}><input className="field" required placeholder="aapl.us · CPIAUCSL · bitcoin:eur · fake.up" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} /></Field>
                <div className="grid grid-cols-3 gap-2">
                  <Field label={t("start")}><input className="field" type="date" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></Field>
                  <Field label={t("end")}><input className="field" type="date" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></Field>
                  <Field label={t("currency")}><input className="field" maxLength={3} value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></Field>
                </div>
                <button className="btn btn-primary" disabled={busy || !form.symbol}>{t("fetch")}</button>
              </form>
            )}
            {tab === "csv" && (
              <form onSubmit={importCsv} className="space-y-2">
                <Field label={t("symbol")}><input className="field" required value={csv.symbol} onChange={(e) => setCsv({ ...csv, symbol: e.target.value })} /></Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label={t("currency")}><input className="field" maxLength={3} value={csv.currency} onChange={(e) => setCsv({ ...csv, currency: e.target.value.toUpperCase() })} /></Field>
                  <Field label={t("unit")}><input className="field" value={csv.unit} onChange={(e) => setCsv({ ...csv, unit: e.target.value })} /></Field>
                  <Field label={t("delimiter")}><input className="field" maxLength={1} placeholder=";" value={csv.delimiter} onChange={(e) => setCsv({ ...csv, delimiter: e.target.value })} /></Field>
                  <Field label={t("decimal")}><input className="field" maxLength={1} placeholder="," value={csv.decimal} onChange={(e) => setCsv({ ...csv, decimal: e.target.value })} /></Field>
                </div>
                <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={csv.dayfirst} onChange={(e) => setCsv({ ...csv, dayfirst: e.target.checked })} />{t("dayfirst")}</label>
                <Field label={t("csv_content")} hint={t("csv_hint")}><textarea className="field" rows={7} required value={csv.text} onChange={(e) => setCsv({ ...csv, text: e.target.value })} /></Field>
                <button className="btn btn-primary" disabled={busy || !csv.text || !csv.symbol}>{t("import")}</button>
              </form>
            )}
            <ErrorBox error={error} t={t} />
            {fetchNotes.map((n, i) => <p key={i} className="help">{n}</p>)}
          </div>
          <div className="panel">
            <h3 className="mb-2">{t("snapshots")} <span className="help">({snapshots.length})</span></h3>
            {snapshots.length === 0 ? <p className="help">{t("snapshots_empty")}</p> : (
              <ul className="m-0 max-h-[300px] list-none space-y-0.5 overflow-y-auto p-0">
                {snapshots.map((s) => (
                  <li key={s.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-white/5">
                      <input type="checkbox" checked={selected.includes(s.id)} onChange={() => toggle(s.id)} aria-label={s.symbol} />
                      <span className="min-w-0 flex-1"><span className="font-semibold">{s.symbol}</span> <span className="help">{s.provider} · {s.actual_start?.slice(0, 4)}–{s.actual_end?.slice(0, 4)} · {s.currency || s.unit || "—"}</span></span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <p className="help mt-2">{t("select_hint")}</p>
          </div>
        </div>
        <div className="min-w-0">
          {chosen.length === 0 && <Empty>{t("market_empty")}</Empty>}
          {chosen.length === 1 && <SeriesView snap={chosen[0]} all={snapshots} />}
          {chosen.length > 1 && <CompareView snaps={chosen} all={snapshots} />}
        </div>
      </div>
    </div>
  );
}
